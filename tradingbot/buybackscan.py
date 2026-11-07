"""
SEC EDGAR Form 8-K buyback authorization scanner.

Scans S&P 100 constituents for 8-K filings with Item 8.01 or 7.01 containing
share repurchase or buyback authorization announcements, saving matching events
to the buyback_events table.
"""

from __future__ import annotations

import argparse
import logging
import sys

from tradingbot.utils.buyback_scan import scan
from tradingbot.utils.config import setup_logging
from tradingbot.utils.db import init_db
from tradingbot.utils.universes import SP100

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SEC EDGAR Form 8-K buyback authorization scanner")
    parser.add_argument(
        "--backfill-days",
        type=int,
        default=5,
        help="Number of days back to scan for 8-K filings (default: 5)",
    )
    args = parser.parse_args(argv)

    setup_logging()
    init_db()

    try:
        inserted = scan(symbols=SP100, since_days=args.backfill_days)
        logger.info("Buyback scan finished: %d event(s) inserted", len(inserted))
        return 0
    except Exception as exc:
        logger.error("Buyback scan failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
