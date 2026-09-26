"""
option_CollarBot — AAPL shares with a collar: a long put below, financed by
a short call above.

A hedge, not an alpha source: the put is bought at AAPL's put skew (the
expensive side) and the call sold near-flat, so a permanent collar pays for
its lower beta with some return. The question the backtest asks is whether
wearing it only some of the time buys the drawdown cut more cheaply.

Rules (utils/option_rules.CollarRules):
  * Hold AAPL shares with ~97% of the book; the rest stays cash for the
    collar's net debit.
  * mode "always": a 0.25-delta put and a 0.25-delta call on the first expiry
    >= 90 days out, one pair per 100 shares, in one transaction.
    "below_sma200" wears it only while AAPL is below its 200-day SMA;
    "iv_cheap" only while ATM IV / 20-day HV <= 1.0 (cheap puts).
  * Roll the collar with 21 days left; take it off when the mode says so.
    Shares under a short call cannot be sold on their own (the margin check
    refuses): the collar comes off first.

Synthetic backtest vs AAPL buy-and-hold: +13.6% vs +23.4%/yr, beta 0.48 vs
1.01, max DD -26% vs -44%, alpha t 1.39 (not significant). "always" beat both
part-time modes; the walk-forward winner did not beat these defaults out of
sample. See docs/backtests/option-bots-round2-2026-09.md. Paper only.

Schedule: 40 15 * * 1-5.
"""

import logging
from typing import ClassVar

from tradingbot.utils import option_math as om
from tradingbot.utils import options
from tradingbot.utils.botclass import Bot
from tradingbot.utils.option_rules import CollarRules, collar_wanted, sma
from tradingbot.utils.runner import run_bot

logger = logging.getLogger(__name__)

UNDERLYING = "AAPL"


class OptionCollarBot(Bot):
    INITIAL_CAPITAL: ClassVar[float] = 100_000.0
    OPTION_ROLL_DTE: ClassVar[int | None] = None
    RULES: ClassVar[CollarRules] = CollarRules()

    def __init__(self, **kwargs):
        super().__init__("option_CollarBot", symbol=UNDERLYING, interval="1d", period="2y", **kwargs)

    def makeOneIteration(self) -> int:
        rules = self.RULES
        data = self.getYFDataWithTA(interval="1d", period="2y", saveToDB=True)
        close = data["close"]
        last = float(close.iloc[-1])

        view = options.load_chain(UNDERLYING, rules.target_dte)
        ratio = None
        if rules.mode == "iv_cheap":
            ratio = om.iv_hv_ratio(options.atm_iv(view), om.historical_volatility(close, rules.hv_window))
        wanted = collar_wanted(last, sma(close, 200), ratio, rules)

        book = self.option_book(UNDERLYING)
        if not book.empty:
            if wanted and (book.dte or 0) > rules.roll_dte:
                self._rebalance_cash(allow_sell=False)
                return 0
            if wanted and not view.live:
                logger.info("Collar due to roll, but the chain is not live; rolling next run")
                return 0
            logger.info("Collar off (%s)", "rolling" if wanted else f"mode {rules.mode} says unhedged")
            self.close_options(UNDERLYING)
        # With the options off, the shares are free: restore the cash buffer in
        # both directions (buying back a short call that finished deep in the
        # money can take more than the buffer holds).
        self._rebalance_cash(allow_sell=True)
        if not wanted:
            return -1 if not book.empty else 0
        if not view.live:
            logger.info("Chain not live; not putting a collar on off-hours")
            return 0
        shares = float(self.dbBot.portfolio.get(UNDERLYING, 0.0))
        n = int(shares // options.CONTRACT_MULTIPLIER)
        if n < 1:
            return 0
        pick = options.select_collar(view, rules.put_delta, rules.call_delta, with_stock=False)
        logger.info("Collar on %d x 100 shares: %s", n, pick.legs)
        self.trade_option_legs([(k, u * n * options.CONTRACT_MULTIPLIER) for k, u in pick.legs])
        return 1

    def _rebalance_cash(self, allow_sell: bool) -> None:
        """Keep cash at cash_buffer of the book: buy shares with any excess, sell shares for a shortfall."""
        cash = float(self.dbBot.portfolio.get("USD", 0.0))
        excess = cash - self.RULES.cash_buffer * self.portfolio_value()
        if excess > 100:
            self.buy(UNDERLYING, quantity_usd=excess)
        elif allow_sell and excess < -100:
            self.sell(UNDERLYING, quantity_usd=-excess)


if __name__ == "__main__":
    run_bot(OptionCollarBot)
