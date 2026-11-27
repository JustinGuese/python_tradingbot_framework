"""Weekly snapshot job: ingests CFTC Commitments of Traders (COT) legacy futures (PLAN-ts151.md S2).

Usage:
    python -m tradingbot.ts151_cotsnapshot
    python -m tradingbot.ts151_cotsnapshot --backfill
"""

from __future__ import annotations

import argparse
import logging
import sys

from tradingbot.utils.ts151.cot import refresh_cot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ts151_cotsnapshot")


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh CFTC COT legacy futures snapshot for TS151")
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Force full backfill from 1986",
    )
    parser.add_argument(
        "--codes",
        type=str,
        default=None,
        help="Comma-separated list of CFTC market codes to fetch (defaults to all commodity universe codes)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Number of days for incremental update (default: 30)",
    )
    args = parser.parse_args()

    codes = None
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]

    logger.info("Starting CFTC COT snapshot job (backfill=%s, days=%d, codes=%s)", args.backfill, args.days, codes or "ALL")
    try:
        count = refresh_cot(codes=codes, backfill=args.backfill, days=args.days)
        logger.info("COT snapshot completed successfully (%d records upserted).", count)
        sys.exit(0)
    except Exception as exc:
        logger.error("COT snapshot job failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
