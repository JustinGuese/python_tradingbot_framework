"""
Daily fundamentals capture for the S&P 100 into `stock_fundamentals`.

yfinance has no fundamentals history, so this CronJob builds one: every
weekday it records each symbol's valuation and ownership figures as reported
that day. Nothing trades on them yet — the point is that a year from now there
is a point-in-time history to backtest against. See utils/fundamentals.py.

Schedule: 0 23 * * 1-5. Exits non-zero when every symbol fails, so a total
outage turns the CronJob red rather than passing as a quiet run.
"""

import logging
import sys

from tradingbot.utils.config import setup_logging
from tradingbot.utils.db import init_db
from tradingbot.utils.fundamentals import snapshot_fundamentals
from tradingbot.utils.universes import SP100

logger = logging.getLogger(__name__)


def main() -> int:
    setup_logging()
    init_db()
    result = snapshot_fundamentals(SP100)
    logger.info(
        "fundamentals snapshot: %d written, %d failed, %d skipped (failed=%s skipped=%s)",
        len(result["written"]),
        len(result["failed"]),
        len(result["skipped"]),
        result["failed"],
        result["skipped"],
    )
    if not result["written"]:
        logger.error("fundamentals snapshot wrote nothing — treating as a failed run")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
