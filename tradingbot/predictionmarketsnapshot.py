"""
Daily prediction-market capture into `prediction_market_snapshots`.

Reads the curated Kalshi and Polymarket series (utils/prediction_market_series.py)
and stores one price per market per US/Eastern day; see
utils/prediction_market_capture.py for how days are assigned and why the job
fails on an all-null series.

Schedule: 30 5 * * * (after midnight Eastern in both summer and winter, so the
previous day's Kalshi candle is complete). Prediction markets trade every day,
so this runs on weekends too. Exits 1 if any series failed or nothing was
written at all, so an API change turns the CronJob red instead of passing
quietly.

`python -m tradingbot.predictionmarketsnapshot --backfill` pulls every market's
whole history (Kalshi from 2021-07, Polymarket from 2023-09); re-running is
harmless.
"""

import argparse
import logging
import sys

from tradingbot.utils.config import setup_logging
from tradingbot.utils.db import init_db
from tradingbot.utils.prediction_market_capture import capture_all
from tradingbot.utils.prediction_market_series import BY_KEY, SERIES

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", action="store_true", help="pull every market's full history")
    parser.add_argument("--series", help="comma-separated series keys (default: all)")
    parser.add_argument("--lookback-days", type=int, default=7, help="daily mode: days re-read per run")
    args = parser.parse_args(argv)
    setup_logging()
    init_db()
    series = tuple(BY_KEY[k] for k in args.series.split(",")) if args.series else SERIES
    result = capture_all(series, backfill=args.backfill, lookback_days=args.lookback_days)
    logger.info(
        "prediction market snapshot: %d new rows %s, %d failed %s",
        result.rows,
        result.written,
        len(result.failed),
        result.failed,
    )
    if not result.rows:
        logger.error("prediction market snapshot wrote nothing — every open market should have a new day")
        return 1
    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
