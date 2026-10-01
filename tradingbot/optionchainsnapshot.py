"""
Daily option-chain capture into `option_quotes`, for SPY, QQQ and the 50
largest S&P 100 names (utils/universes.OPTION_CAPTURE_UNIVERSE).

yfinance serves no historical option chains, so this CronJob builds the
history: one live snapshot per weekday, near the close, of ~9 expiries per name
(7 days .. 18 months) and the strikes around the money. See
utils/option_capture.py for what is kept and why.

Schedule: 45 16,19 * * 1-5. A run captures only within CAPTURE_WINDOW_MINUTES
of that day's NYSE close (utils/market_calendar.py):
  * On a regular day that is the 19:45 run: 15:45 New York in summer, 14:45 in
    winter.
  * On an early close (13:00 New York: the day after Thanksgiving, Christmas
    Eve, the eve of Independence Day) it is the 16:45 run. That is 12:45 EDT
    or 11:45 EST, before the bell.
  * The other run logs why it skipped and exits 0.

Without the early run, a half-day capture landed after the close and wrote
nothing, the same as a holiday. A manual run outside the window needs --force.

A closed market (holiday) exits 0 with nothing written. A run where the
market was open but every symbol failed exits 1, so an outage turns the
CronJob red rather than passing quietly.

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
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from tradingbot.utils import market_calendar
from tradingbot.utils.config import setup_logging
from tradingbot.utils.db import init_db
from tradingbot.utils.fundamentals import historical_market_caps
from tradingbot.utils.option_capture import capture_universe
from tradingbot.utils.universes import OPTION_CAPTURE_UNIVERSE
from tradingbot.utils.vol_surface import (
    CLOSE_HISTORY_YEARS,
    captured_dates,
    download_closes,
    snapshot_implied_correlation,
    snapshot_vol_surface,
)

logger = logging.getLogger(__name__)

CAPTURE_WINDOW_MINUTES = 90


def capture_due(now: datetime) -> tuple[bool, str]:
    """Whether a run at `now` is the day's capture: within the window before the close."""
    left = market_calendar.minutes_to_close(now)
    if left is None:
        return False, "not an NYSE session"
    if left <= 0:
        early = market_calendar.is_early_close(now.astimezone(market_calendar.NEW_YORK).date())
        return False, f"market closed {-left:.0f} min ago" + (
            " (early close, captured by the earlier run)" if early else ""
        )
    if left > CAPTURE_WINDOW_MINUTES:
        return False, f"{left:.0f} min to the close; the capture runs in the last {CAPTURE_WINDOW_MINUTES}"
    return True, f"{left:.0f} min to the close"


def derive(day=None, closes=None, r=None, caps=None) -> bool:
    """
    vol_surface + implied correlation + mispricing scan for one day. False on
    failure. `caps` ({symbol: market cap} as of the day) weights names that
    stock_fundamentals has no row for.
    """
    from tradingbot.utils.mispricing_scan import snapshot_scan

    try:
        surface = snapshot_vol_surface(day, closes=closes, r=r)
        if not surface["written"]:
            logger.error("vol surface wrote nothing for %s", day or "today")
            return False
        snapshot_implied_correlation(day, fallback_weights=caps)
        snapshot_scan(day, r=r)
    except Exception:
        logger.exception("deriving the vol surface for %s failed", day or "today")
        return False
    return True


def caps_on(caps: pd.DataFrame, day: date) -> dict[str, float]:
    """The last market cap per symbol on or before `day` (as stock_fundamentals is read live)."""
    if caps.empty:
        return {}
    known = caps[caps.index <= pd.Timestamp(day)]
    if known.empty:
        return {}
    last = known.iloc[-1].dropna()
    return {u: float(v) for u, v in last.items() if v > 0}


def rate_on(irx: pd.Series, day: date) -> float | None:
    """^IRX as a decimal on `day` (the last print on or before it); None if there is none."""
    known = irx[irx.index.date <= day].dropna()
    return float(known.iloc[-1]) / 100.0 if len(known) else None


def backfill(since: date | None = None) -> int:
    """
    Rebuild the derived tables for every captured date from `since`, oldest
    first: each day's scan z-scores read the vrp history the days before it
    wrote. Closes, ^IRX and market caps (implied-correlation weights) are
    downloaded once and cut at each day, so no day sees later prices, today's
    rate or today's caps.
    """
    days = [d for d in captured_dates() if since is None or d >= since]
    if not days:
        logger.info("backfill: no captured dates")
        return 0
    start = (pd.Timestamp(days[0]) - pd.DateOffset(years=CLOSE_HISTORY_YEARS)).date()
    closes = download_closes(sorted(OPTION_CAPTURE_UNIVERSE), start)
    irx = download_closes(["^IRX"], start).get("^IRX", pd.Series(dtype=float))
    members = sorted(set(OPTION_CAPTURE_UNIVERSE) - {"SPY", "QQQ"})
    caps = historical_market_caps(members, days[0] - timedelta(days=10))
    failed = []
    for i, day in enumerate(days):
        if not derive(day, closes=closes, r=rate_on(irx, day), caps=caps_on(caps, day)):
            failed.append(day)
        if i % 20 == 0:
            logger.info("backfill: %d/%d days done (%s), %d failed", i + 1, len(days), day, len(failed))
    logger.info("backfill: %d days, %d failed %s", len(days), len(failed), failed)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", action="store_true", help="rebuild the derived tables for every captured date")
    parser.add_argument("--since", type=date.fromisoformat, help="with --backfill: only dates from this one on")
    parser.add_argument("--force", action="store_true", help="capture now, whatever the time to the close")
    args = parser.parse_args(argv)
    setup_logging()
    init_db()
    if args.backfill:
        return backfill(args.since)
    due, why = capture_due(datetime.now(UTC))
    if not due and not args.force:
        logger.info("option chain snapshot: skipped, %s", why)
        return 0
    logger.info("option chain snapshot: capturing, %s", why)
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
