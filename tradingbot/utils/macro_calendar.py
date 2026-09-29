"""
Scheduled macro releases: FOMC decisions, CPI prints and jobs reports.

Index implied vol runs up into these and falls after them, and a 10-delta
condor opened the day before one sells the event at whatever the market
charges for it. `macro_events` holds the dates; option bots read the next one.

Sources:
- CPI (FRED release 10) and the Employment Situation / nonfarm payrolls
  (FRED release 50) come from FRED's release calendar, which includes
  future scheduled dates. Needs FRED_API_KEY; refreshed weekly by
  tradingbot/macrocalendarsnapshot.py.
- FOMC is static. FRED's "FOMC Press Release" (release 101) is updated every
  day with the target-rate series, so its release dates do not mark meetings.
  FOMC_STATEMENT_DATES below is the statement day (the last day) of every
  scheduled meeting, plus the unscheduled meetings that announced a rate
  change, transcribed from federalreserve.gov (fomccalendars.htm and
  fomchistorical<year>.htm, read 2026-09-28). Conference calls and notation
  votes without a statement are left out. Append each new year's schedule
  when the Fed publishes it; `stale_warning` says when the table runs out.
"""

import logging
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta

import httpx
import numpy as np
import pandas as pd

from tradingbot.utils.db import MacroEvent, get_db_session

logger = logging.getLogger(__name__)
# httpx logs every request URL at INFO, and FRED takes the API key as a query
# parameter: without this the key lands in plain text in every pod's logs.
logging.getLogger("httpx").setLevel(logging.WARNING)

FRED_URL = "https://api.stlouisfed.org/fred/release/dates"
FRED_RELEASES = {"CPI": 10, "NFP": 50}
KINDS = ("FOMC", "CPI", "NFP")
# A bot whose newest known future event is further out than this treats the
# calendar as stale: CPI and NFP are monthly, so a live table always has one.
STALE_AFTER_DAYS = 45

_FOMC = {
    2006: "01-31 03-28 05-10 06-29 08-08 09-20 10-25 12-12",
    2007: "01-31 03-21 05-09 06-28 08-07 08-17 09-18 10-31 12-11",  # 08-17 unscheduled
    2008: "01-22 01-30 03-18 04-30 06-25 08-05 09-16 10-08 10-29 12-16",  # 01-22, 10-08 unscheduled
    2009: "01-28 03-18 04-29 06-24 08-12 09-23 11-04 12-16",
    2010: "01-27 03-16 04-28 06-23 08-10 09-21 11-03 12-14",
    2011: "01-26 03-15 04-27 06-22 08-09 09-21 11-02 12-13",
    2012: "01-25 03-13 04-25 06-20 08-01 09-13 10-24 12-12",
    2013: "01-30 03-20 05-01 06-19 07-31 09-18 10-30 12-18",
    2014: "01-29 03-19 04-30 06-18 07-30 09-17 10-29 12-17",
    2015: "01-28 03-18 04-29 06-17 07-29 09-17 10-28 12-16",
    2016: "01-27 03-16 04-27 06-15 07-27 09-21 11-02 12-14",
    2017: "02-01 03-15 05-03 06-14 07-26 09-20 11-01 12-13",
    2018: "01-31 03-21 05-02 06-13 08-01 09-26 11-08 12-19",
    2019: "01-30 03-20 05-01 06-19 07-31 09-18 10-30 12-11",
    2020: "01-29 03-03 03-15 04-29 06-10 07-29 09-16 11-05 12-16",  # 03-03, 03-15 unscheduled
    2021: "01-27 03-17 04-28 06-16 07-28 09-22 11-03 12-15",
    2022: "01-26 03-16 05-04 06-15 07-27 09-21 11-02 12-14",
    2023: "02-01 03-22 05-03 06-14 07-26 09-20 11-01 12-13",
    2024: "01-31 03-20 05-01 06-12 07-31 09-18 11-07 12-18",
    2025: "01-29 03-19 05-07 06-18 07-30 09-17 10-29 12-10",
    2026: "01-28 03-18 04-29 06-17 07-29 09-16 10-28 12-09",
    2027: "01-27 03-17 04-28 06-09 07-28 09-15 10-27 12-08",
}
FOMC_STATEMENT_DATES: tuple[date, ...] = tuple(
    date.fromisoformat(f"{year}-{md}") for year, days in _FOMC.items() for md in days.split()
)


def fetch_fred_release_dates(
    release_id: int, start: str, end: str, api_key: str, client: httpx.Client | None = None
) -> list[date]:
    """Every date FRED lists for `release_id` in [start, end], future scheduled ones included."""
    params = {
        "release_id": release_id,
        "api_key": api_key,
        "file_type": "json",
        "realtime_start": start,
        "realtime_end": "9999-12-31",
        "include_release_dates_with_no_data": "true",
        "sort_order": "asc",
        "limit": 10000,
    }
    owned = client is None
    client = client or httpx.Client(timeout=30)
    try:
        response = client.get(FRED_URL, params=params)
        response.raise_for_status()
        rows = response.json().get("release_dates", [])
    finally:
        if owned:
            client.close()
    days = sorted({date.fromisoformat(r["date"]) for r in rows if "date" in r})
    return [d for d in days if start <= d.isoformat() <= end]


def _store(kind: str, days: Iterable[date], source: str) -> int:
    """Insert the (kind, day) pairs not stored yet. Returns how many were new."""
    days = sorted(set(days))
    if not days:
        return 0
    with get_db_session() as session:
        have = {
            row.event_date
            for row in session.query(MacroEvent).filter(
                MacroEvent.kind == kind, MacroEvent.event_date >= days[0], MacroEvent.event_date <= days[-1]
            )
        }
        new = [d for d in days if d not in have]
        session.add_all([MacroEvent(kind=kind, event_date=d, source=source) for d in new])
    return len(new)


def refresh_macro_events(api_key: str, start: str = "2000-01-01", end: str = "2030-12-31") -> dict[str, int]:
    """Store FOMC (static) and CPI/NFP (FRED) dates. Idempotent.

    Returns {kind: dates FRED/the table returned}, so a caller can tell an empty
    answer from "nothing new". Raises on a FRED error: a silent partial refresh
    is what the weekly job exists to catch.
    """
    counts = {"FOMC": len(FOMC_STATEMENT_DATES)}
    new = _store("FOMC", FOMC_STATEMENT_DATES, "federalreserve.gov")
    with httpx.Client(timeout=30) as client:
        for kind, release_id in FRED_RELEASES.items():
            days = fetch_fred_release_dates(release_id, start, end, api_key, client=client)
            counts[kind] = len(days)
            new += _store(kind, days, f"FRED release {release_id}")
    logger.info("macro events: %s dates, %d new", counts, new)
    return counts


def upcoming_events(today: date | None = None, kinds: Iterable[str] = KINDS, days: int = 60) -> list[tuple[str, date]]:
    """(kind, date) of every event from today to today + days, soonest first."""
    today = today or datetime.now(UTC).date()
    end = today + timedelta(days=days)
    with get_db_session() as session:
        rows = (
            session.query(MacroEvent.kind, MacroEvent.event_date)
            .filter(MacroEvent.kind.in_(list(kinds)), MacroEvent.event_date >= today, MacroEvent.event_date <= end)
            .order_by(MacroEvent.event_date)
            .all()
        )
    return [(kind, d) for kind, d in rows]


def next_event(today: date | None = None, kinds: Iterable[str] = ("FOMC", "CPI")) -> tuple[str, date] | None:
    """The next event of `kinds` on or after today, or None if the table has none."""
    events = upcoming_events(today, kinds, days=STALE_AFTER_DAYS)
    return events[0] if events else None


def next_event_in(
    events: pd.DataFrame, today: date, kinds: Iterable[str] = ("FOMC", "CPI")
) -> tuple[tuple[str, date] | None, int | None]:
    """
    next_event and business_days_to_next_event over preloaded rows
    (macro_events_frame), for backtests that ask once per replayed day.
    """
    kinds = set(kinds)
    end = today + timedelta(days=STALE_AFTER_DAYS)
    for kind, day in events.sort_values("event_date").itertuples(index=False):
        if kind in kinds and today <= day <= end:
            return (kind, day), _busdays(today, day)
    return None, None


def _busdays(today: date, day: date) -> int:
    return int(np.busday_count(today, day))


def business_days_to_next_event(today: date | None = None, kinds: Iterable[str] = ("FOMC", "CPI")) -> int | None:
    """Business days from today to the next event: 0 = today, 1 = tomorrow. None if unknown."""
    today = today or datetime.now(UTC).date()
    event = next_event(today, kinds)
    if event is None:
        return None
    return _busdays(today, event[1])


def stale_warning(today: date | None = None) -> str | None:
    """A message when the calendar cannot be trusted, else None.

    CPI and NFP land every month, so no event in the next STALE_AFTER_DAYS
    means the weekly refresh has stopped (or never ran).
    """
    if not upcoming_events(today, ("CPI", "NFP"), days=STALE_AFTER_DAYS):
        return f"macro calendar has no CPI/NFP in the next {STALE_AFTER_DAYS} days: refresh job not running?"
    return None


def macro_events_frame(kinds: Iterable[str] = ("FOMC", "CPI"), start: str = "2000-01-01") -> pd.DataFrame:
    """All stored events of `kinds` from `start` as a (kind, event_date) frame, for backtests."""
    with get_db_session() as session:
        rows = (
            session.query(MacroEvent.kind, MacroEvent.event_date)
            .filter(MacroEvent.kind.in_(list(kinds)), MacroEvent.event_date >= date.fromisoformat(start))
            .order_by(MacroEvent.event_date)
            .all()
        )
    return pd.DataFrame(rows, columns=["kind", "event_date"])


def bdays_to_next_event_series(index: pd.DatetimeIndex, event_dates: Iterable[date]) -> pd.Series:
    """For each day in `index`, business days to the next event on or after it (NaN past the last)."""
    events = np.array(sorted(set(event_dates)), dtype="datetime64[D]")
    days = index.values.astype("datetime64[D]")
    pos = np.searchsorted(events, days, side="left")
    out = np.full(len(days), np.nan)
    ok = pos < len(events)
    out[ok] = np.busday_count(days[ok], events[pos[ok]])
    return pd.Series(out, index=index)
