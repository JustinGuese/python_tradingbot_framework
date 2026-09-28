"""
Daily refresh of earnings dates (with timing) and ex-dividend dates into the DB.

The option bots read these through options.earnings_events / next_earnings_date
/ earnings_history / next_dividend, which answer from the stored tables when a
symbol was refreshed within 3 days and fall back to yfinance otherwise. See
utils/corporate_events.py.

Universe: the option capture universe (SPY, QQQ, the 50 names), every
underlying an option bot holds, and AAPL (the round-1/2 option bots' name).

Schedule: 0 12 * * 1-5, before the 14:30 and 15:00 UTC option runs. Exits 1
when fewer than 80% of the 50 single names came back with earnings, so a
yfinance outage turns the CronJob red instead of passing quietly.
"""

import logging
import sys

from tradingbot.utils import options
from tradingbot.utils.config import setup_logging
from tradingbot.utils.corporate_events import refresh_corporate_events
from tradingbot.utils.db import Bot, get_db_session, init_db
from tradingbot.utils.universes import OPTION_CAPTURE_UNIVERSE

logger = logging.getLogger(__name__)

MIN_EARNINGS_COVERAGE = 0.8
ALWAYS = ("AAPL",)


def option_underlyings_held() -> set[str]:
    """Underlyings of every option leg any bot holds."""
    with get_db_session() as session:
        portfolios = [dict(b.portfolio or {}) for b in session.query(Bot).all()]
    return set().union(*(options.option_underlyings(p) for p in portfolios)) if portfolios else set()


def universe() -> list[str]:
    return sorted(set(OPTION_CAPTURE_UNIVERSE) | option_underlyings_held() | set(ALWAYS))


def main() -> int:
    setup_logging()
    init_db()
    symbols = universe()
    stats = refresh_corporate_events(symbols)
    single_names = set(OPTION_CAPTURE_UNIVERSE[2:])
    coverage = len(stats.with_earnings & single_names) / len(single_names)
    logger.info(
        "corporate events: %d symbols, earnings +%d / %d updated, dividends +%d / %d updated, "
        "%.0f%% of the 50 names with earnings, failed=%s",
        stats.symbols,
        stats.earnings_added,
        stats.earnings_updated,
        stats.dividends_added,
        stats.dividends_updated,
        coverage * 100,
        stats.failed,
    )
    if coverage < MIN_EARNINGS_COVERAGE:
        logger.error(
            "Only %.0f%% of the 50 names returned earnings (need %.0f%%) — treating as a failed run",
            coverage * 100,
            MIN_EARNINGS_COVERAGE * 100,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
