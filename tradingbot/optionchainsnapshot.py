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

After a successful capture it condenses the day into `vol_surface` (one row
per name: constant-maturity IV, skew, term slope, VRP, GEX, put/call, max
pain), SPY's `implied_correlation`, and the ranked `option_mispricing_scan`
list. A failure there exits 1 too: a missing surface day is a hole in the
history every IV rank is computed from.

`python -m tradingbot.optionchainsnapshot --backfill` rebuilds those derived
tables for every date already in option_quotes (also the path for imported
historical chains).
"""

import argparse
import logging
import sys

from tradingbot.utils.config import setup_logging
from tradingbot.utils.db import init_db
from tradingbot.utils.option_capture import capture_universe
from tradingbot.utils.universes import OPTION_CAPTURE_UNIVERSE
from tradingbot.utils.vol_surface import captured_dates, snapshot_implied_correlation, snapshot_vol_surface

logger = logging.getLogger(__name__)


def derive(day=None) -> bool:
    """vol_surface + implied correlation + mispricing scan for one day. False on failure."""
    from tradingbot.utils.mispricing_scan import snapshot_scan

    try:
        surface = snapshot_vol_surface(day)
        if not surface["written"]:
            logger.error("vol surface wrote nothing for %s", day or "today")
            return False
        snapshot_implied_correlation(day)
        snapshot_scan(day)
    except Exception:
        logger.exception("deriving the vol surface for %s failed", day or "today")
        return False
    return True


def backfill() -> int:
    days = captured_dates()
    failed = [d for d in days if not derive(d)]
    logger.info("backfill: %d days, %d failed %s", len(days), len(failed), failed)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", action="store_true", help="rebuild the derived tables for every captured date")
    args = parser.parse_args(argv)
    setup_logging()
    init_db()
    if args.backfill:
        return backfill()
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
    return 0 if derive() else 1


if __name__ == "__main__":
    sys.exit(main())
