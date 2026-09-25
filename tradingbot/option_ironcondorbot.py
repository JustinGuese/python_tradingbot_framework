"""
option_IronCondorBot — sell AAPL iron condors in a rich-vol, range-bound tape.

From the options course: "Iron condor — the strategy profits if the stock price
stays between the short call and short put. And when volatility is high, that
range can be assumed to be higher than usual." Sell when implied vol is well
above historical, never through a news event.

Rules (utils/option_rules.CreditRules, walk-forward re-tuned 2026-09-25):
  * Only when ATM IV / 20-day HV >= 1.15, ADX(14) < 25 (no strong trend to run
    through one side), ^VIX < 35, and no AAPL earnings before expiry.
  * Short put and short call at 0.20 delta, $35 wings, first expiry >= 60 DTE.
    Wide wings matter: a $10 wing buys back most of the vol the short leg
    sells, leaving almost no net vega to collect the variance premium with.
  * Size: worst-case loss (wider wing - credit) <= 25% of the book.
  * Close at 75% of the credit, at a loss of 2x the credit, or at 7 DTE.

Re-tune vs the original (0.16 delta, $10 wings, 35 DTE, 50% / 21 DTE exits),
picked on 2012-2019 and judged on 2019-2026: out-of-sample alpha +0.5%/yr at
t 0.11 (+2.5%/yr, beta 0.12, max DD -19%) against -11%/yr at t -2.42. That is
"stopped losing", not "found an edge". See docs/backtests/option-bots-2026-09.md.

Only one side of a condor can finish in the money, so the framework reserves
one wing's width, not two. Paper only.

Not built: the earnings iron condor (sell just before earnings, buy back after
the IV crush). Its edge is single-name earnings IV, which nothing we have can
price or backtest; option_quotes will have the data after a few earnings cycles.

Schedule: 10 15 * * 1-5.
"""

import logging
from typing import ClassVar

from tradingbot.utils import option_math as om
from tradingbot.utils import options
from tradingbot.utils.botclass import Bot
from tradingbot.utils.option_rules import CreditRules, credit_exit_reason, earnings_clear, premium_selling_ok
from tradingbot.utils.runner import run_bot

logger = logging.getLogger(__name__)

UNDERLYING = "AAPL"


class OptionIronCondorBot(Bot):
    INITIAL_CAPITAL: ClassVar[float] = 100_000.0
    OPTION_ROLL_DTE: ClassVar[int | None] = None
    RULES: ClassVar[CreditRules] = CreditRules(
        short_delta=0.20,
        width=35.0,
        target_dte=60,
        take_profit=0.75,
        exit_dte=7,
        min_iv_hv=1.15,
        max_adx=25.0,
    )

    def __init__(self, **kwargs):
        super().__init__("option_IronCondorBot", symbol=UNDERLYING, interval="1d", period="2y", **kwargs)

    def makeOneIteration(self) -> int:
        rules = self.RULES
        book = self.option_book(UNDERLYING)
        if not book.empty:
            reason = credit_exit_reason(book.credit, book.pnl, book.dte, rules)
            logger.info(
                "Holding condor: credit %.2f, P&L %.2f, %s DTE, delta %.1f, theta %.2f/day, vega %.2f",
                book.credit,
                book.pnl,
                book.dte,
                book.greeks.delta,
                book.greeks.theta,
                book.greeks.vega,
            )
            if reason:
                logger.info("Closing: %s", reason)
                self.close_options(UNDERLYING)
                return -1
            return 0

        data = self.getYFDataWithTA(interval="1d", period="2y", saveToDB=True)
        close = data["close"]
        adx = float(data["trend_adx"].iloc[-1])

        view = options.load_chain(UNDERLYING, rules.target_dte)
        if not view.live:
            logger.info("Chain not live; not opening a condor off-hours")
            return 0
        ratio = om.iv_hv_ratio(options.atm_iv(view), om.historical_volatility(close, rules.hv_window))
        vix = self.getLatestPrice("^VIX")
        logger.info("IV/HV %s, ADX %.1f, VIX %.1f", f"{ratio:.2f}" if ratio else "n/a", adx, vix)
        if not premium_selling_ok(ratio, vix, adx, rules):
            return 0
        if not earnings_clear(options.next_earnings_date(UNDERLYING, view.today), view.expiry, view.today):
            logger.info("Earnings before %s; not selling premium through it", view.expiry)
            return 0

        opened = self.open_iron_condor(
            UNDERLYING,
            short_delta=rules.short_delta,
            width=rules.width,
            dte=rules.target_dte,
            max_risk_usd=rules.max_risk_pct * self.portfolio_value(),
            view=view,
        )
        return 1 if opened else 0


if __name__ == "__main__":
    run_bot(OptionIronCondorBot)
