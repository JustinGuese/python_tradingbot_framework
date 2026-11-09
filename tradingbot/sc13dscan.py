"""
SEC EDGAR Schedule 13D scanner (CronJob wrapper).

Scans the frozen activist universe (S&P 100/400/600) for initial 13D filings and stores them in `sc13d_events`.
The default 45-day lookback makes the first run seed the whole live book of Activist13DBot; later runs only find
what is new. Writes a heartbeat row after every successful scan.
"""

from __future__ import annotations

import argparse
import logging
import sys

from tradingbot.utils.config import setup_logging
from tradingbot.utils.db import init_db
from tradingbot.utils.sc13d_scan import DEFAULT_SINCE_DAYS, scan, touch_heartbeat
from tradingbot.utils.universes import ACTIVIST_UNIVERSE

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SEC EDGAR Schedule 13D scanner")
    parser.add_argument("--backfill-days", type=int, default=DEFAULT_SINCE_DAYS, help="days of filings to scan")
    args = parser.parse_args(argv)

    setup_logging()
    init_db()
    try:
        inserted = scan(symbols=ACTIVIST_UNIVERSE, since_days=args.backfill_days)
        touch_heartbeat()
        logger.info("13D scan finished: %d event(s) inserted", len(inserted))
        return 0
    except Exception as exc:
        logger.error("13D scan failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
