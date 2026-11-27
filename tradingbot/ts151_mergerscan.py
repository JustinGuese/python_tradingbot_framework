"""Daily/Intraday scan job: scans EDGAR form index for merger announcements (PLAN-ts151.md S3).

Usage:
    python -m tradingbot.ts151_mergerscan
    python -m tradingbot.ts151_mergerscan --backfill 2005
"""

from __future__ import annotations

import argparse
import logging
import sys

from tradingbot.utils.ts151.merger_scan import scan_mergers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ts151_mergerscan")


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan EDGAR form index for merger announcements")
    parser.add_argument(
        "--backfill",
        type=int,
        nargs="?",
        const=2005,
        default=None,
        help="Run full historical backfill starting from YEAR (defaults to 2005 if flag present without argument)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=5,
        help="Number of past days to scan for incremental updates (default: 5)",
    )
    args = parser.parse_args()

    logger.info("Starting merger scan job (backfill=%s, days=%d)", args.backfill, args.days)
    try:
        count = scan_mergers(backfill_year=args.backfill, days=args.days)
        logger.info("Merger scan completed successfully (%d events upserted).", count)
        sys.exit(0)
    except Exception as exc:
        logger.error("Merger scan job failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
