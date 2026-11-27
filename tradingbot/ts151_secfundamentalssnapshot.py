"""Weekly snapshot job: ingests SEC XBRL fundamental companyfacts (PLAN-ts151.md S1).

Usage:
    python -m tradingbot.ts151_secfundamentalssnapshot
    python -m tradingbot.ts151_secfundamentalssnapshot --tickers AAPL,MSFT,NVDA
"""

from __future__ import annotations

import argparse
import logging
import sys

from tradingbot.utils.ts151.sec_fundamentals import refresh_sec_fundamentals

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ts151_secfundamentalssnapshot")


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh SEC XBRL fundamentals snapshot for TS151")
    parser.add_argument(
        "--tickers",
        type=str,
        default=None,
        help="Comma-separated list of tickers to refresh (defaults to SP500 ∪ SP100)",
    )
    args = parser.parse_args()

    tickers = None
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    logger.info("Starting SEC fundamentals snapshot job (tickers=%s)", tickers or "ALL_UNIVERSE")
    success_count, fail_count = refresh_sec_fundamentals(tickers=tickers)

    total = success_count + fail_count
    if total == 0:
        logger.warning("No tickers evaluated.")
        sys.exit(0)

    fail_ratio = fail_count / total
    logger.info(
        "Summary: %d total, %d succeeded, %d failed (failure ratio: %.2f%%)",
        total,
        success_count,
        fail_count,
        fail_ratio * 100,
    )

    if fail_ratio > 0.20:
        logger.error("Failure ratio %.2f%% exceeded maximum allowed threshold (20%%)", fail_ratio * 100)
        sys.exit(1)

    logger.info("SEC fundamentals snapshot job finished successfully.")
    sys.exit(0)


if __name__ == "__main__":
    main()
