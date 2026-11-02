"""
Intraday session utilities: RTH filtering, VWAP, opening range, and session contracts.

Pure, stateless helpers shared between live execution and backtests:
  * DST-safe session filtering using America/New_York and market_calendar.
  * Session VWAP with zero-volume fallback.
  * Opening range calculation.
  * Trade / SessionResult frozen dataclasses and exit resolution.
  * IntradayRules protocol and simulate_session runner.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, NamedTuple, Protocol, runtime_checkable
from zoneinfo import ZoneInfo

import pandas as pd

from . import market_calendar as mc

NEW_YORK = ZoneInfo("America/New_York")
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)


def _extract_timestamps(bars: pd.DataFrame) -> pd.Series:
    """Extract and parse timestamps from a bar DataFrame's columns or index."""
    if bars.empty:
        return pd.Series(dtype="datetime64[ns, UTC]")

    col_map = {str(col).lower(): col for col in bars.columns}
    for candidate in ("timestamp", "datetime", "date"):
        if candidate in col_map:
            raw = bars[col_map[candidate]]
            return pd.to_datetime(raw)

    if isinstance(bars.index, pd.DatetimeIndex):
        return bars.index.to_series()

    try:
        return pd.to_datetime(bars.index.to_series())
    except Exception as e:
        raise ValueError("DataFrame has neither a timestamp/datetime/date column nor a DatetimeIndex") from e


def session_bars(
    bars: pd.DataFrame,
    session_date: date | str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """
    Filter a bar frame to one New York session date and regular trading hours (RTH).

    Timezone-aware: handles Eastern Daylight Time (EDT) and Eastern Standard Time (EST)
    transparently. On early close sessions (13:00 NY), the window closes early.
    If session_date is None, filters to the session of the last bar in the DataFrame.
    """
    if bars.empty:
        return bars.copy()

    ts = _extract_timestamps(bars)
    ts_ny = ts.dt.tz_localize(NEW_YORK) if ts.dt.tz is None else ts.dt.tz_convert(NEW_YORK)

    if session_date is None:
        target_date = ts_ny.iloc[-1].date()
    elif isinstance(session_date, str):
        target_date = pd.Timestamp(session_date).date()
    elif isinstance(session_date, (datetime, pd.Timestamp)):
        target_date = session_date.date()
    else:
        target_date = session_date

    open_time = REGULAR_OPEN
    close_time = EARLY_CLOSE if mc.is_early_close(target_date) else REGULAR_CLOSE

    bar_dates = ts_ny.dt.date
    bar_times = ts_ny.dt.time

    mask = (bar_dates == target_date) & (bar_times >= open_time) & (bar_times < close_time)
    return bars.loc[mask].copy()


# PascalCase alias per PLAN.md specification
SessionBars = session_bars


def session_vwap(bars: pd.DataFrame) -> pd.Series:
    """
    Cumulative typical-price * volume / volume for the session.

    Falls back to typical price when cumulative volume is 0.
    """
    if bars.empty:
        return pd.Series(dtype=float, index=bars.index, name="vwap")

    col_map = {str(col).lower(): col for col in bars.columns}
    for col in ("high", "low", "close"):
        if col not in col_map:
            raise KeyError(f"Missing required column '{col}' for VWAP calculation")

    high = bars[col_map["high"]].astype(float)
    low = bars[col_map["low"]].astype(float)
    close = bars[col_map["close"]].astype(float)

    typical_price = (high + low + close) / 3.0

    if "volume" in col_map:
        vol = bars[col_map["volume"]].astype(float).fillna(0.0)
    else:
        vol = pd.Series(0.0, index=bars.index, dtype=float)

    cum_tp_vol = (typical_price * vol).cumsum()
    cum_vol = vol.cumsum()

    vwap = pd.Series(index=bars.index, dtype=float, name="vwap")
    pos_vol_mask = cum_vol > 0
    vwap.loc[pos_vol_mask] = cum_tp_vol.loc[pos_vol_mask] / cum_vol.loc[pos_vol_mask]
    vwap.loc[~pos_vol_mask] = typical_price.loc[~pos_vol_mask]
    return vwap


class OpeningRange(NamedTuple):
    high: float
    low: float
    end_ts: pd.Timestamp | None


def opening_range(bars: pd.DataFrame, minutes: int = 15) -> OpeningRange:
    """
    Return (high, low, end_ts) for the first `minutes` of the session.
    """
    if bars.empty:
        return OpeningRange(high=float("nan"), low=float("nan"), end_ts=None)

    ts_series = _extract_timestamps(bars)
    start_ts = pd.Timestamp(ts_series.iloc[0])
    end_ts = start_ts + pd.Timedelta(minutes=minutes)

    or_mask = ts_series < end_ts
    or_bars = bars.loc[or_mask]
    if or_bars.empty:
        or_bars = bars.iloc[:1]

    col_map = {str(col).lower(): col for col in or_bars.columns}
    high_col = col_map.get("high", "high")
    low_col = col_map.get("low", "low")

    high = float(or_bars[high_col].max())
    low = float(or_bars[low_col].min())
    return OpeningRange(high=high, low=low, end_ts=end_ts)


class ExitResult(NamedTuple):
    price: float | None
    reason: str | None

    def __bool__(self) -> bool:
        return self.price is not None


def resolve_exit(
    bar: pd.Series | dict[str, Any],
    stop: float,
    target: float,
) -> ExitResult:
    """
    Check if a bar touches the stop or target.

    Stop wins if one bar touches both (conservative tie-break).
    """
    bar_dict = bar.to_dict() if isinstance(bar, pd.Series) else dict(bar)

    col_map = {str(k).lower(): v for k, v in bar_dict.items()}
    if "low" not in col_map or "high" not in col_map:
        raise KeyError("Bar must contain 'low' and 'high' prices")

    low = float(col_map["low"])
    high = float(col_map["high"])

    # Stop wins if both touched
    if low <= stop:
        return ExitResult(price=float(stop), reason="stop")
    elif high >= target:
        return ExitResult(price=float(target), reason="target")
    else:
        return ExitResult(price=None, reason=None)


@dataclass(frozen=True)
class Trade:
    entry_ts: pd.Timestamp | datetime
    entry: float
    stop: float
    target: float
    exit_ts: pd.Timestamp | datetime
    exit: float
    exit_reason: str
    r_multiple: float


@dataclass(frozen=True)
class SessionResult:
    date: date
    trade: Trade | None = None


@runtime_checkable
class IntradayRules(Protocol):
    def simulate_session(self, bars: pd.DataFrame) -> SessionResult: ...


def simulate_session(bars: pd.DataFrame, rules: IntradayRules) -> SessionResult:
    """Simulate a single session using the provided rules."""
    return rules.simulate_session(bars)


def session_close_ts(session: pd.DataFrame) -> pd.Timestamp:
    """RTH close of the session's date (13:00 on early closes), as a New York timestamp."""
    ts = _extract_timestamps(session)
    first = ts.iloc[0]
    first_ny = first.tz_localize(NEW_YORK) if first.tzinfo is None else first.tz_convert(NEW_YORK)
    day = first_ny.date()
    close = EARLY_CLOSE if mc.is_early_close(day) else REGULAR_CLOSE
    return pd.Timestamp(datetime.combine(day, close), tz=NEW_YORK)


def session_open_ts(session: pd.DataFrame) -> pd.Timestamp:
    """RTH open (09:30 New York) of the session's date."""
    ts = _extract_timestamps(session)
    first = ts.iloc[0]
    first_ny = first.tz_localize(NEW_YORK) if first.tzinfo is None else first.tz_convert(NEW_YORK)
    return pd.Timestamp(datetime.combine(first_ny.date(), REGULAR_OPEN), tz=NEW_YORK)


def bar_minutes(session: pd.DataFrame) -> float:
    """Median bar spacing in minutes (5.0 if there is only one bar)."""
    ts = _extract_timestamps(session)
    if len(ts) < 2:
        return 5.0
    return float(ts.diff().dropna().median().total_seconds() / 60.0)


def to_ny(ts: pd.Timestamp) -> pd.Timestamp:
    """Normalise a timestamp (naive means UTC) to New York time."""
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC").tz_convert(NEW_YORK) if t.tzinfo is None else t.tz_convert(NEW_YORK)


def walk_to_exit(
    session: pd.DataFrame,
    entry_pos: int,
    stop: float,
    target: float,
    force_exit_ts: pd.Timestamp,
) -> Trade:
    """
    Enter at the close of ``session.iloc[entry_pos]`` and walk later bars to an exit.

    Bars at or after ``force_exit_ts`` are never traded on. Stop wins ties
    (``resolve_exit``). With no stop or target hit the trade exits at the last
    allowed bar's close: reason ``"time"`` once the forced exit has been reached
    (a bar at or after it exists, or the next bar would start at it), otherwise
    ``"open"`` — the session is still in progress and the position is live.
    """
    ts = _extract_timestamps(session).reset_index(drop=True)
    rows = session.reset_index(drop=True)
    entry = float(rows["close"].iloc[entry_pos])
    risk = entry - stop
    step = pd.Timedelta(minutes=bar_minutes(session))
    force = to_ny(force_exit_ts)

    def trade(pos: int, price: float, reason: str) -> Trade:
        return Trade(
            entry_ts=ts.iloc[entry_pos],
            entry=entry,
            stop=stop,
            target=target,
            exit_ts=ts.iloc[pos],
            exit=price,
            exit_reason=reason,
            r_multiple=(price - entry) / risk,
        )

    last = entry_pos
    for pos in range(entry_pos + 1, len(rows)):
        if to_ny(ts.iloc[pos]) >= force:
            return trade(last, float(rows["close"].iloc[last]), "time")
        hit = resolve_exit(rows.iloc[pos], stop, target)
        if hit:
            return trade(pos, float(hit.price or stop), str(hit.reason))
        last = pos
    done = to_ny(ts.iloc[last]) + step >= force
    return trade(last, float(rows["close"].iloc[last]), "time" if done else "open")
