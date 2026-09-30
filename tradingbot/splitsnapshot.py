"""
Detect stock splits and apply them: to the cached price history and to every
bot's share counts. See utils/splits.py.

Universe: every symbol a bot holds (option underlyings included) plus every
symbol with cached bars from the last 30 days, in one batched yfinance request.

Schedule: hourly through the US session on weekdays, so a split is normally
applied before most bots trade on its ex-date. A bot that runs first still
applies it at the start of its next run, exactly (the reconstruction uses the
trade log).

`--period 2y` backfills older splits into split_events and fixes their cached
history; books are only touched for splits inside the 14-day apply window.

Exits 1 if yfinance returned nothing at all, or if any bot's book could not be
adjusted (a non-whole-number split of a held option), so the CronJob turns red.
"""

import argparse
import logging
import sys

from tradingbot.utils.config import setup_logging
from tradingbot.utils.db import init_db
from tradingbot.utils.splits import ratio_label, run_sweep, sweep_universe

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--period", default="1mo", help="yfinance period to scan for splits (default 1mo)")
    args = parser.parse_args(argv)

    setup_logging()
    init_db()
    symbols = sweep_universe()
    result = run_sweep(symbols, period=args.period)
    if result is None:
        logger.error("yfinance returned no data for any of %d symbols — treating as a failed run", len(symbols))
        return 1
    logger.info(
        "splits: %d symbols scanned, %d new split(s) %s, history rescaled %s, %d book(s) adjusted, failed=%s",
        result.symbols,
        len(result.new),
        [f"{s.symbol} {ratio_label(s.ratio)} {s.ex_date}" for s in result.new],
        {f"{k[0]} {k[1]}": v for k, v in result.history.items() if v},
        len(result.applied),
        result.failed,
    )
    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
