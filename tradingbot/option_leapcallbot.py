"""
option_LeapCallBot — AAPL stock replacement with a 70-delta LEAP call.

From the options course: "LEAP — 1-2 years in future, 70 delta", for a
long-term bullish view, with a lower cost basis than the shares; and "buy
options when implied volatility is low".

Rules (utils/option_rules.LeapRules):
  * Hold while AAPL closes above its 200-day SMA; exit when it closes more
    than 3% below it (the buffer avoids whipsaw around the line).
  * Enter only when ATM IV / 60-day HV <= 1.2: don't buy expensive time.
  * 0.70-delta call, first expiry >= 540 days out, sized so its delta-dollars
    are ~1.0x the book. That is ~25% of the book in premium, the rest cash.
    That is the "lower cost basis" part: the same stock exposure for a quarter
    of the capital, and the most that can be lost is the premium.
  * The framework rolls the LEAP into a fresh 540-day, 0.70-delta call when
    180 days are left, before theta decay accelerates.

This is a beta strategy (delta ~1x AAPL, and AAPL's beta to QQQ is ~1), so
judge it on alpha vs QQQ with that in mind. The trend filter is the only
source of alpha it has. Paper only.

Schedule: 0 15 * * 1-5.
"""

import logging
from typing import ClassVar

from tradingbot.utils import option_math as om
from tradingbot.utils import options
from tradingbot.utils.botclass import Bot
from tradingbot.utils.option_rules import LeapRules, leap_contracts, leap_signal, sma
from tradingbot.utils.runner import run_bot

logger = logging.getLogger(__name__)

UNDERLYING = "AAPL"
_RULES = LeapRules()


class OptionLeapCallBot(Bot):
    INITIAL_CAPITAL: ClassVar[float] = 100_000.0
    OPTION_TARGET_DTE: ClassVar[int] = _RULES.target_dte
    OPTION_ROLL_DTE: ClassVar[int | None] = _RULES.roll_dte
    OPTION_TARGET_DELTA: ClassVar[float | None] = _RULES.delta
    RULES: ClassVar[LeapRules] = _RULES

    def __init__(self, **kwargs):
        super().__init__("option_LeapCallBot", symbol=UNDERLYING, interval="1d", period="2y", **kwargs)

    def makeOneIteration(self) -> int:
        rules = self.RULES
        data = self.getYFDataWithTA(interval="1d", period="2y", saveToDB=True)
        close = data["close"]
        last = float(close.iloc[-1])
        signal = leap_signal(last, sma(close, 200), rules)
        book = self.option_book(UNDERLYING)

        if not book.empty:
            logger.info(
                "Holding %.0f contracts: P&L %.2f (%.0f%%), %s DTE, delta-dollars %.0f",
                sum(p.contracts for p in book.positions),
                book.pnl,
                100 * book.pnl_pct,
                book.dte,
                book.greeks.delta * book.spot,
            )
            if signal == -1:
                logger.info("Close %.2f fell below SMA200 band; exiting", last)
                self.close_options(UNDERLYING)
                return -1
            return 0
        if signal != 1:
            return 0

        view = options.load_chain(UNDERLYING, rules.target_dte)
        if not view.live:
            logger.info("Chain not live; not buying off-hours")
            return 0
        ratio = om.iv_hv_ratio(options.atm_iv(view), om.historical_volatility(close, rules.hv_window))
        if ratio is None or ratio > rules.max_iv_hv:
            logger.info("IV/HV %s above %.2f: time is expensive, waiting", ratio, rules.max_iv_hv)
            return 0

        contract = options.select_contract(UNDERLYING, "C", rules.target_dte, delta=rules.delta, view=view)
        quote = options.latest_quote(contract, max_age=None)
        ask = quote.ask if quote and quote.ask > 0 else self.getLatestPrice(contract) * 1.02
        n = leap_contracts(self.portfolio_value(), view.spot, rules.delta, rules.leverage)
        if n < 1:
            return 0
        # A hair over n contracts at the ask: buy() floors to whole contracts
        # and keeps the change as cash.
        self.buy(UNDERLYING, quantity_usd=n * ask * options.CONTRACT_MULTIPLIER * 1.005, option="call")
        return 1


if __name__ == "__main__":
    run_bot(OptionLeapCallBot)
