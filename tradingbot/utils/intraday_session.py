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
