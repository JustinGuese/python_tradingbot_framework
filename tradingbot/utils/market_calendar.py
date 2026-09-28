"""
The NYSE session calendar: holidays and early closes.

Until 2026-09-28 the framework only knew weekdays.
  * A holiday counted as a trading day in every business-day count: HAR
    horizons, holding periods, "the session before the ex-date".
  * On an early close (13:00 New York: the day after Thanksgiving, Christmas
    Eve, the eve of Independence Day), the 19:45 UTC chain capture and
    EarningsCrush's 19:30 entry ran after the bell. They then quietly did
    nothing, which looks the same as a holiday.

It wraps exchange_calendars' XNYS. Counts use numpy's business-day functions
with the calendar's weekday holidays as the holiday list, so a count costs
about what np.busday_count does. Outside the calendar's window (1995 to two
years ahead) days fall back to weekdays with a regular 16:00 close.
"""

from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars as xc
import numpy as np
import pandas as pd

NEW_YORK = ZoneInfo("America/New_York")
REGULAR_CLOSE = time(16, 0)
CALENDAR_START = "1995-01-01"
FORWARD_YEARS = 2


@lru_cache(maxsize=1)
def _calendar() -> xc.ExchangeCalendar:
    end = datetime.now(UTC).date() + timedelta(days=365 * FORWARD_YEARS)
    return xc.get_calendar("XNYS", start=CALENDAR_START, end=end.isoformat())


@lru_cache(maxsize=1)
def _holidays() -> np.ndarray:
    """Weekdays inside the calendar's window that are not sessions."""
    cal = _calendar()
    weekdays = pd.bdate_range(cal.first_session, cal.last_session)
    return np.array(weekdays.difference(cal.sessions).date, dtype="datetime64[D]")


@lru_cache(maxsize=1)
def _early_closes() -> frozenset[date]:
    return frozenset(pd.DatetimeIndex(_calendar().early_closes).date)


def _in_window(day: date) -> bool:
    cal = _calendar()
    return cal.first_session.date() <= day <= cal.last_session.date()


def is_session(day: date) -> bool:
    """True if NYSE trades on `day`."""
    return bool(np.is_busday(np.datetime64(day, "D"), holidays=_holidays()))


def sessions_between(start: date, end: date) -> int:
    """Sessions from start (inclusive) to end (exclusive); negative when end < start. Like np.busday_count."""
    return int(np.busday_count(np.datetime64(start, "D"), np.datetime64(end, "D"), holidays=_holidays()))


def next_session(day: date) -> date:
    """The first session strictly after `day`."""
    nxt = np.busday_offset(np.datetime64(day + timedelta(days=1), "D"), 0, roll="forward", holidays=_holidays())
    return pd.Timestamp(nxt).date()


def is_early_close(day: date) -> bool:
    """True on a session that closes early (13:00 New York)."""
    return day in _early_closes()


def session_close_utc(day: date) -> datetime | None:
    """When `day`'s session closes, as an aware UTC datetime; None if it is not a session."""
    if not is_session(day):
        return None
    if _in_window(day):
        return _calendar().session_close(pd.Timestamp(day)).to_pydatetime().astimezone(UTC)
    return datetime.combine(day, REGULAR_CLOSE, tzinfo=NEW_YORK).astimezone(UTC)


def minutes_to_close(now: datetime) -> float | None:
    """Minutes from `now` to today's close (negative once closed); None when today is not a session."""
    now = now if now.tzinfo else now.replace(tzinfo=UTC)
    close = session_close_utc(now.astimezone(NEW_YORK).date())
    return None if close is None else (close - now).total_seconds() / 60.0
