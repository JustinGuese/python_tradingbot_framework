"""
option_CatalystCallBot — an AAPL call bought before earnings, sold before the announcement.

From the options course: "long call, delta .75, in the money, choose if you are
bullish"; "expiration date — when I expect that it will be after the event,
like earnings"; "check if some catalyst (earnings, ...) is inside your
expiration date — this is what you want"; and "buy options when implied
volatility is very low".

The tension in those rules is that IV is at its highest right before earnings,
so a call bought then pays for the post-earnings crush. The resolution is
timing: buy early, while IV is still ordinary, and sell the day before the
report. That captures the pre-earnings drift and the run-up in implied vol
without holding through the crush.

Rules (utils/option_rules.CatalystRules):
  * Enter 10-25 trading days before the next AAPL earnings date, if close >
    SMA50 (bullish) and ATM IV / 20-day HV <= 1.0 (options not rich).
  * 0.75-delta call, first expiry >= 14 days after earnings (the catalyst
    sits inside the expiration), 30% of the book in premium.
  * Exit the trading day before earnings, at +50%, or at -40%.

Paper only. Four setups a year at most, so its record will stay statistically
thin for a long time.

Schedule: 15 15 * * 1-5.
"""

import logging
from typing import ClassVar

from tradingbot.utils import option_math as om
from tradingbot.utils import options
from tradingbot.utils.botclass import Bot
from tradingbot.utils.option_rules import CatalystRules, catalyst_entry_ok, catalyst_exit_reason, sma
from tradingbot.utils.runner import run_bot

logger = logging.getLogger(__name__)

UNDERLYING = "AAPL"


class OptionCatalystCallBot(Bot):
    INITIAL_CAPITAL: ClassVar[float] = 100_000.0
    OPTION_ROLL_DTE: ClassVar[int | None] = None  # always sold before earnings, never rolled
    RULES: ClassVar[CatalystRules] = CatalystRules()

    def __init__(self, **kwargs):
        super().__init__("option_CatalystCallBot", symbol=UNDERLYING, interval="1d", period="2y", **kwargs)

    def makeOneIteration(self) -> int:
        rules = self.RULES
        today = options.utc_today()
        earnings = options.next_earnings_date(UNDERLYING, today)
        book = self.option_book(UNDERLYING)

        if not book.empty:
            expiry = min(p.contract.expiry for p in book.positions)
            reason = catalyst_exit_reason(today, earnings, expiry, book.pnl_pct, rules)
            logger.info("Holding: P&L %.0f%%, earnings %s, expiry %s", 100 * book.pnl_pct, earnings, expiry)
            if reason:
                logger.info("Closing: %s", reason)
                self.close_options(UNDERLYING)
                return -1
            return 0

        if not catalyst_entry_ok(today, earnings, rules):
            logger.info("Outside the entry window (next earnings %s)", earnings)
            return 0
        assert earnings is not None  # catalyst_entry_ok is False without a date
        data = self.getYFDataWithTA(interval="1d", period="2y", saveToDB=True)
        close = data["close"]
        if float(close.iloc[-1]) <= sma(close, rules.trend_window):
            logger.info("Below SMA%d: not bullish", rules.trend_window)
            return 0

        dte = (earnings - today).days + rules.days_after_earnings
        view = options.load_chain(UNDERLYING, dte)
        if not view.live:
            logger.info("Chain not live; not buying off-hours")
            return 0
        ratio = om.iv_hv_ratio(options.atm_iv(view), om.historical_volatility(close, rules.hv_window))
        if ratio is None or ratio > rules.max_iv_hv:
            logger.info("IV/HV %s above %.2f: options not cheap, waiting", ratio, rules.max_iv_hv)
            return 0

        self.buy(
            UNDERLYING,
            quantity_usd=rules.position_pct * self.portfolio_value(),
            option="call",
            delta=rules.delta,
            dte=dte,
        )
        return 1


if __name__ == "__main__":
    run_bot(OptionCatalystCallBot)
