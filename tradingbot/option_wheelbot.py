"""
option_WheelBot — the wheel on AAPL: cash-secured puts until assigned, then
covered calls on the assigned shares until they are called away.

The documented edge behind it is the volatility risk premium: implied vol
usually exceeds the vol the stock then delivers, so option writers win more
often than they lose — and AAPL's put skew makes the puts the richer side.
The cost is the tail: an assigned put in a crash holds the stock all the way
down, with the call premium as the only cushion.

Rules (utils/option_rules.WheelRules):
  * No shares: sell a 0.30-delta put on the first expiry >= 35 days out,
    cash-secured — as many contracts as free cash covers at the strike.
  * 100+ shares (assigned): sell 0.30-delta covered calls, one per 100
    shares, never struck below the shares' cost (the assignment strike).
  * Buy the short back at 50% of its credit and sell the next one on the
    following run; otherwise it expires. OPTION_SETTLEMENT = "physical": an
    ITM put at expiry assigns 100 shares per contract at the strike, an ITM
    covered call delivers them.

Synthetic backtest: +18.4%/yr with beta 0.77 and alpha t 0.86. That is AAPL at
a lower beta, not an edge (alpha -2.9%/yr in 2012-2019, +8.5% in 2019-2026,
the half where AAPL itself had +9.7%). The walk-forward winner lost to these
defaults out of sample, so they stay. See
docs/backtests/option-bots-round2-2026-09.md. Paper only.

Schedule: 25 15 * * 1-5.
"""

import logging
from typing import ClassVar

from tradingbot.utils import option_math as om
from tradingbot.utils import options
from tradingbot.utils.botclass import Bot
from tradingbot.utils.option_rules import (
    WheelRules,
    earnings_clear,
    short_premium_exit_reason,
    sma,
    wheel_call_floor,
    wheel_put_ok,
)
from tradingbot.utils.runner import run_bot

logger = logging.getLogger(__name__)

UNDERLYING = "AAPL"


class OptionWheelBot(Bot):
    INITIAL_CAPITAL: ClassVar[float] = 100_000.0
    OPTION_ROLL_DTE: ClassVar[int | None] = None
    OPTION_SETTLEMENT: ClassVar[str] = "physical"
    RULES: ClassVar[WheelRules] = WheelRules()

    def __init__(self, **kwargs):
        super().__init__("option_WheelBot", symbol=UNDERLYING, interval="1d", period="2y", **kwargs)

    def makeOneIteration(self) -> int:
        rules = self.RULES
        book = self.option_book(UNDERLYING)
        if not book.empty:
            reason = short_premium_exit_reason(book.credit, book.pnl, book.dte, rules.take_profit, rules.exit_dte)
            logger.info(
                "Holding %s: credit %.2f, P&L %.2f, %s DTE, delta %.1f",
                [p.key for p in book.positions],
                book.credit,
                book.pnl,
                book.dte,
                book.greeks.delta,
            )
            if reason:
                logger.info("Buying back: %s", reason)
                self.close_options(UNDERLYING)
                return -1
            return 0

        shares = float(self.dbBot.portfolio.get(UNDERLYING, 0.0))
        view = options.load_chain(UNDERLYING, rules.target_dte)
        if not view.live:
            logger.info("Chain not live; not selling off-hours")
            return 0

        if shares >= options.CONTRACT_MULTIPLIER:
            return self._sell_covered_calls(view, shares)
        if shares > 1e-6:
            # An odd lot left after a partial call-away cannot back a contract.
            logger.info("Selling odd lot of %.4f %s before the next put", shares, UNDERLYING)
            self.sell(UNDERLYING)

        data = self.getYFDataWithTA(interval="1d", period="2y", saveToDB=True)
        close = data["close"]
        ratio = om.iv_hv_ratio(options.atm_iv(view), om.historical_volatility(close, rules.hv_window))
        earnings_ok = earnings_clear(options.next_earnings_date(UNDERLYING, view.today), view.expiry, view.today)
        if not wheel_put_ok(ratio, float(close.iloc[-1]), sma(close, 200), earnings_ok, rules):
            logger.info("Put gate closed (IV/HV %s, earnings clear %s)", ratio, earnings_ok)
            return 0
        pick = options.select_short_leg(view, "P", rules.put_delta)
        opened = self.open_structure(pick)
        return 1 if opened else 0

    def _sell_covered_calls(self, view: options.ChainView, shares: float) -> int:
        rules = self.RULES
        cost = options.entry_value(self.bot_name, UNDERLYING) / shares if shares else None
        floor = wheel_call_floor(cost, rules)
        try:
            pick = options.select_short_leg(view, "C", rules.call_delta, min_strike=floor)
        except ValueError as e:
            logger.info("No covered call at or above cost %.2f: %s", floor or 0.0, e)
            return 0
        n = int(shares // options.CONTRACT_MULTIPLIER)
        logger.info("Selling %d covered calls %s (share cost %.2f)", n, pick.legs, cost or 0.0)
        self.trade_option_legs([(k, u * n * options.CONTRACT_MULTIPLIER) for k, u in pick.legs])
        return 1


if __name__ == "__main__":
    run_bot(OptionWheelBot)
