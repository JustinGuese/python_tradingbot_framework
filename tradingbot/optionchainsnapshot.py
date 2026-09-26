"""
Daily option-chain capture into `option_quotes`, for SPY, QQQ and the 50
largest S&P 100 names (utils/universes.OPTION_CAPTURE_UNIVERSE).

yfinance serves no historical option chains, so this CronJob builds the
history: one live snapshot per weekday, near the close, of ~9 expiries per name
(7 days .. 18 months) and the strikes around the money. See
utils/option_capture.py for what is kept and why.

Schedule: 45 19 * * 1-5 (15:45 New York in summer, 14:45 in winter: the
market is open either way). A closed market (holiday) exits 0 with nothing
written; a run where the market was open but every symbol failed exits 1, so an
outage turns the CronJob red rather than passing quietly.
"""

import logging
import sys

from tradingbot.utils.config import setup_logging
from tradingbot.utils.db import init_db
from tradingbot.utils.option_capture import capture_universe
from tradingbot.utils.universes import OPTION_CAPTURE_UNIVERSE

logger = logging.getLogger(__name__)


def main() -> int:
    setup_logging()
    init_db()
    result = capture_universe(OPTION_CAPTURE_UNIVERSE)
    if result.market_closed:
        logger.info("option chain snapshot: market closed, nothing to capture")
        return 0
    logger.info(
        "option chain snapshot: %d rows for %d symbols, %d failed %s",
        result.rows,
        len(result.written),
        len(result.failed),
        result.failed,
    )
    if not result.written:
        logger.error("option chain snapshot wrote nothing while the market was open — failed run")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
