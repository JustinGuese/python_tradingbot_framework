"""
option_IndexVolBot — sell SPY iron condors when implied vol beats a forecast
of the vol SPY will actually deliver.

The AAPL option bots found that the gap between implied vol and a HAR-RV
forecast predicts the variance premium, but a short structure on one stock
gives it back in single-name jumps. An index jumps less and pays the larger
premium, and nobody picked SPY with hindsight.

Rules (utils/option_rules.IndexVolRules):
  * Fair vol: HAR-RV forecast of SPY's realized vol over the trading days to
    expiry (option_math.har_rv_forecast). No earnings term: an index has none.
  * Sell only when ATM IV - fair vol >= min_gap and ^VIX < 40.
  * Short put at put_delta and short call at call_delta on the first expiry
    >= target_dte days out, each with a long wing width_pct of spot further
    out. Worst-case loss (a wing minus the credit) <= max_risk_pct of the book.
  * Close at take_profit of the credit, at a loss of stop_loss x the credit,
    or at exit_dte days left.

Walk-forward (2026-09-26, synthetic: Black-Scholes at 0.85 x ^VIX with the
live chain's skew, 2000-2026). The textbook defaults (16-delta, 5% wings, 45
DTE) made t 0.75 and lost on 2013-2026. The consensus of the 2000-2013 top 10
ships instead: 10-delta shorts, 10% wings, 35 DTE, exit at 7 DTE, and only when
ATM IV >= HAR fair + 3 points. Alpha +2.0%/yr at t 4.28, beta 0.01, max DD
-6.4%, positive in both halves. It also holds at double costs (t 3.16), on QQQ
(t 2.39) and with half the skew. The single 2000-2013 winner, without the gap
gate, failed out of sample; the gate is what matters. Small but uncorrelated.
See docs/backtests/index-vol-2026-09.md. Paper only.

Round 3 (2026-09-28) tested VIX/VIX3M and VVIX entry gates, an FOMC blackout,
a term-structure unwind, Yang-Zhang and GARCH forecasts, a z-scored signal and
a monthly tail hedge on top of these rules: none beat them out of sample, so
all stay off (docs/backtests/option-round3-2026-09.md). The bot still logs
the vol curve and the next FOMC/CPI date on every run.

Schedule: 45 15 * * 1-5 (the chain must be live to open).
"""

import logging
from typing import ClassVar

from tradingbot.utils import macro_calendar, options
from tradingbot.utils import option_math as om
from tradingbot.utils.botclass import Bot
from tradingbot.utils.option_rules import (
    IndexVolRules,
    business_days,
    daily_close,
    index_vol_entry_ok,
    index_vol_exit_reason,
    index_vol_unwind_reason,
)
from tradingbot.utils.runner import run_bot
from tradingbot.utils.vol_indices import VIX, VIX3M, VVIX, latest_vol_indices, term_ratio

logger = logging.getLogger(__name__)

UNDERLYING = "SPY"


class OptionIndexVolBot(Bot):
    INITIAL_CAPITAL: ClassVar[float] = 100_000.0
    OPTION_ROLL_DTE: ClassVar[int | None] = None
    RULES: ClassVar[IndexVolRules] = IndexVolRules(
        target_dte=35, put_delta=0.10, call_delta=0.10, width_pct=0.10, min_gap=0.03, exit_dte=7
    )

    def __init__(self, **kwargs):
        super().__init__("option_IndexVolBot", symbol=UNDERLYING, interval="1d", period="5y", **kwargs)

    @staticmethod
    def _next_event(today):
        """(kind, date) of the next FOMC/CPI release and sessions until it; (None, None) if unknown."""
        try:
            stale = macro_calendar.stale_warning(today)
            if stale:
                logger.warning(stale)
            event = macro_calendar.next_event(today)
            return event, macro_calendar.business_days_to_next_event(today) if event else None
        except Exception as exc:
            logger.warning("Macro calendar unavailable: %s", exc)
            return None, None

    def makeOneIteration(self) -> int:
        rules = self.RULES
        book = self.option_book(UNDERLYING)
        if not book.empty:
            reason = index_vol_exit_reason(book.credit, book.pnl, book.dte, rules)
            if reason is None and rules.unwind_term_ratio is not None:
                reason = index_vol_unwind_reason(
                    term_ratio(self.getLatestPrice(VIX), self.getLatestPrice(VIX3M)), rules
                )
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

        view = options.load_chain(UNDERLYING, rules.target_dte)
        if not view.live:
            logger.info("Chain not live; not opening a condor off-hours")
            return 0
        close = daily_close(self.getYFData(interval="1d", period="5y", saveToDB=True))
        fair = om.har_rv_forecast(om.log_returns(close), max(business_days(view.today, view.expiry), 1))
        iv = options.atm_iv(view)
        curve = latest_vol_indices(self.getLatestPrice, (VIX, VIX3M, VVIX))
        vix, term = curve[VIX], term_ratio(curve[VIX], curve[VIX3M])
        event, bdays_to_event = self._next_event(view.today)
        logger.info(
            "%s %s: ATM IV %s vs HAR fair %.1f%% (gap %s), VIX %s, VIX/VIX3M %s, VVIX %s, next event %s",
            UNDERLYING,
            view.expiry,
            f"{iv:.1%}" if iv else "n/a",
            fair * 100,
            f"{iv - fair:+.1%}" if iv else "n/a",
            f"{vix:.1f}" if vix else "n/a",
            f"{term:.2f}" if term else "n/a",
            f"{curve[VVIX]:.0f}" if curve[VVIX] else "n/a",
            f"{event[0]} {event[1]} ({bdays_to_event} sessions)" if event else "unknown",
        )
        if not index_vol_entry_ok(
            iv, fair, vix, rules, term_ratio=term, vvix=curve[VVIX], bdays_to_event=bdays_to_event
        ):
            return 0

        opened = self.open_iron_condor(
            UNDERLYING,
            short_delta=rules.put_delta,
            width=rules.width_pct * view.spot,
            dte=rules.target_dte,
            max_risk_usd=rules.max_risk_pct * self.portfolio_value(),
            view=view,
            call_delta=rules.call_delta,
        )
        return 1 if opened else 0


if __name__ == "__main__":
    run_bot(OptionIndexVolBot)
