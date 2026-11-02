"""Tests for intraday session utilities (tradingbot/utils/intraday_session.py)."""

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils.intraday_session import (
    NEW_YORK,
    IntradayRules,
    OpeningRange,
    SessionBars,
    SessionResult,
    Trade,
    opening_range,
    resolve_exit,
    session_bars,
    session_vwap,
    simulate_session,
)


def _make_day_bars(session_date: date, tz_name: str = "UTC") -> pd.DataFrame:
    """
    Generate synthetic 5-minute bars covering full 24 hours of session_date in UTC.
    Includes open, high, low, close, volume.
    """
    start_dt = datetime.combine(session_date, datetime.min.time(), tzinfo=UTC)
    # 288 5-minute intervals in a 24-hour day
    timestamps = [start_dt + pd.Timedelta(minutes=5 * i) for i in range(288)]
    np.random.seed(42)
    base_price = 450.0
    opens = [base_price + i * 0.1 for i in range(len(timestamps))]
    highs = [p + 0.5 for p in opens]
    lows = [p - 0.5 for p in opens]
    closes = [p + 0.2 for p in opens]
    volumes = [1000 + i * 10 for i in range(len(timestamps))]

    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(timestamps),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    )
    if tz_name != "UTC":
        df["timestamp"] = df["timestamp"].dt.tz_convert(ZoneInfo(tz_name))
    return df


def test_dst_boundary_march():
    """
    US DST begins second Sunday in March (March 8, 2026).
    Friday 2026-03-06 is EST (UTC-5): RTH 09:30-16:00 EST = 14:30-21:00 UTC.
    Monday 2026-03-09 is EDT (UTC-4): RTH 09:30-16:00 EDT = 13:30-20:00 UTC.
    """
    fri_bars = _make_day_bars(date(2026, 3, 6))
    mon_bars = _make_day_bars(date(2026, 3, 9))
    combined = pd.concat([fri_bars, mon_bars], ignore_index=True)

    # Filter Friday
    fri_rth = session_bars(combined, date(2026, 3, 6))
    assert len(fri_rth) == 78  # 390 min / 5 min = 78 bars
    assert fri_rth["timestamp"].iloc[0] == datetime(2026, 3, 6, 14, 30, tzinfo=UTC)
    assert fri_rth["timestamp"].iloc[-1] == datetime(2026, 3, 6, 20, 55, tzinfo=UTC)

    # Verify New York local time is exactly 09:30 to 15:55
    fri_ny = fri_rth["timestamp"].dt.tz_convert(NEW_YORK)
    assert fri_ny.iloc[0].strftime("%H:%M") == "09:30"
    assert fri_ny.iloc[-1].strftime("%H:%M") == "15:55"

    # Filter Monday
    mon_rth = SessionBars(combined, date(2026, 3, 9))
    assert len(mon_rth) == 78
    assert mon_rth["timestamp"].iloc[0] == datetime(2026, 3, 9, 13, 30, tzinfo=UTC)
    assert mon_rth["timestamp"].iloc[-1] == datetime(2026, 3, 9, 19, 55, tzinfo=UTC)

    mon_ny = mon_rth["timestamp"].dt.tz_convert(NEW_YORK)
    assert mon_ny.iloc[0].strftime("%H:%M") == "09:30"
    assert mon_ny.iloc[-1].strftime("%H:%M") == "15:55"


def test_dst_boundary_november():
    """
    US DST ends first Sunday in November (November 1, 2026).
    Friday 2026-10-30 is EDT (UTC-4): RTH 09:30-16:00 EDT = 13:30-20:00 UTC.
    Monday 2026-11-02 is EST (UTC-5): RTH 09:30-16:00 EST = 14:30-21:00 UTC.
    """
    fri_bars = _make_day_bars(date(2026, 10, 30))
    mon_bars = _make_day_bars(date(2026, 11, 2))
    combined = pd.concat([fri_bars, mon_bars], ignore_index=True)

    fri_rth = session_bars(combined, date(2026, 10, 30))
    assert len(fri_rth) == 78
    assert fri_rth["timestamp"].iloc[0] == datetime(2026, 10, 30, 13, 30, tzinfo=UTC)
    assert fri_rth["timestamp"].iloc[-1] == datetime(2026, 10, 30, 19, 55, tzinfo=UTC)

    mon_rth = session_bars(combined, date(2026, 11, 2))
    assert len(mon_rth) == 78
    assert mon_rth["timestamp"].iloc[0] == datetime(2026, 11, 2, 14, 30, tzinfo=UTC)
    assert mon_rth["timestamp"].iloc[-1] == datetime(2026, 11, 2, 20, 55, tzinfo=UTC)


def test_early_close_and_partial_days():
    """Early-close days (13:00 NY) and partial or empty frames do not raise."""
    # 2026-11-27 is the day after Thanksgiving (early close at 13:00 NY)
    full_day = _make_day_bars(date(2026, 11, 27))
    early_bars = session_bars(full_day, date(2026, 11, 27))
    # 09:30 to 13:00 is 210 minutes = 42 5-min bars
    assert len(early_bars) == 42
    ny_times = early_bars["timestamp"].dt.tz_convert(NEW_YORK)
    assert ny_times.iloc[0].strftime("%H:%M") == "09:30"
    assert ny_times.iloc[-1].strftime("%H:%M") == "12:55"

    # Partial day (e.g. only 5 bars)
    partial = early_bars.iloc[:5].copy()
    assert len(session_bars(partial, date(2026, 11, 27))) == 5

    # Empty frame does not raise
    empty = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    res = session_bars(empty, date(2026, 11, 27))
    assert res.empty


def test_session_bars_default_date_and_index():
    """When session_date is None, defaults to the last bar's session; supports DatetimeIndex."""
    fri_bars = _make_day_bars(date(2026, 3, 6))
    mon_bars = _make_day_bars(date(2026, 3, 9))
    combined = pd.concat([fri_bars, mon_bars], ignore_index=True)

    # Without session_date, picks Monday (last bar)
    rth = session_bars(combined)
    assert len(rth) == 78
    assert rth["timestamp"].iloc[0] == datetime(2026, 3, 9, 13, 30, tzinfo=UTC)

    # Frame with DatetimeIndex instead of column
    df_idx = combined.set_index("timestamp")
    rth_idx = session_bars(df_idx, date(2026, 3, 6))
    assert len(rth_idx) == 78
    assert rth_idx.index[0] == datetime(2026, 3, 6, 14, 30, tzinfo=UTC)


def test_session_vwap_manual_calc():
    """VWAP matches manual calculations across standard, zero, and falling-back volume."""
    # Hand-built 3 bars:
    # Bar 0: tp = (10 + 8 + 9) / 3 = 9.0, vol = 100 -> cum_tp_vol = 900, cum_vol = 100, vwap = 9.0
    # Bar 1: tp = (12 + 10 + 11) / 3 = 11.0, vol = 200 -> cum_tp_vol = 900 + 2200 = 3100, cum_vol = 300, vwap = 3100/300 = 10.333333
    # Bar 2: tp = (15 + 13 + 14) / 3 = 14.0, vol = 0 -> cum_tp_vol = 3100, cum_vol = 300, vwap = 3100/300 = 10.333333
    df = pd.DataFrame(
        {
            "high": [10.0, 12.0, 15.0],
            "low": [8.0, 10.0, 13.0],
            "close": [9.0, 11.0, 14.0],
            "volume": [100.0, 200.0, 0.0],
        }
    )
    vwap = session_vwap(df)
    assert np.isclose(vwap.iloc[0], 9.0)
    assert np.isclose(vwap.iloc[1], 3100.0 / 300.0)
    assert np.isclose(vwap.iloc[2], 3100.0 / 300.0)

    # Initial zero volume falls back to typical price
    df_zero_start = pd.DataFrame(
        {
            "high": [10.0, 20.0],
            "low": [8.0, 10.0],
            "close": [9.0, 15.0],
            "volume": [0.0, 100.0],
        }
    )
    vwap_zero = session_vwap(df_zero_start)
    assert np.isclose(vwap_zero.iloc[0], 9.0)  # typical price fallback
    assert np.isclose(vwap_zero.iloc[1], 15.0)  # (15 * 100) / 100

    # All zero volume falls back to typical price for each bar
    df_all_zero = pd.DataFrame(
        {
            "high": [10.0, 20.0],
            "low": [8.0, 16.0],
            "close": [9.0, 18.0],
            "volume": [0.0, 0.0],
        }
    )
    vwap_all_zero = session_vwap(df_all_zero)
    assert np.isclose(vwap_all_zero.iloc[0], 9.0)
    assert np.isclose(vwap_all_zero.iloc[1], 18.0)

    # Empty frame
    assert session_vwap(pd.DataFrame(columns=["high", "low", "close", "volume"])).empty


def test_opening_range():
    """opening_range returns (high, low, end_ts) for the specified minutes."""
    bars = _make_day_bars(date(2026, 3, 6))
    rth = session_bars(bars, date(2026, 3, 6))

    # First 15 minutes = first 3 bars (09:30, 09:35, 09:40)
    res = opening_range(rth, minutes=15)
    assert isinstance(res, tuple)
    assert isinstance(res, OpeningRange)

    first_3 = rth.iloc[:3]
    assert res.high == float(first_3["high"].max())
    assert res.low == float(first_3["low"].min())
    assert res.end_ts == rth["timestamp"].iloc[0] + pd.Timedelta(minutes=15)

    # Unpacking works
    h, low_val, end_ts = opening_range(rth, minutes=15)
    assert h == res.high
    assert low_val == res.low
    assert end_ts == res.end_ts

    # Partial / empty does not raise
    empty = pd.DataFrame(columns=["timestamp", "high", "low"])
    empty_res = opening_range(empty, minutes=15)
    assert np.isnan(empty_res.high)
    assert np.isnan(empty_res.low)
    assert empty_res.end_ts is None


def test_resolve_exit():
    """Exit resolution prioritizes stop when one bar touches both."""
    # Target only
    bar_target = {"high": 105.0, "low": 99.0}
    res_target = resolve_exit(bar_target, stop=95.0, target=100.0)
    assert res_target.price == 100.0
    assert res_target.reason == "target"
    assert bool(res_target) is True

    # Stop only
    bar_stop = {"high": 98.0, "low": 92.0}
    res_stop = resolve_exit(bar_stop, stop=95.0, target=100.0)
    assert res_stop.price == 95.0
    assert res_stop.reason == "stop"
    assert bool(res_stop) is True

    # One bar touches BOTH stop and target -> stop wins
    bar_both = {"high": 105.0, "low": 92.0}
    res_both = resolve_exit(bar_both, stop=95.0, target=100.0)
    assert res_both.price == 95.0
    assert res_both.reason == "stop"

    # Neither hit
    bar_neither = {"high": 99.0, "low": 96.0}
    res_neither = resolve_exit(bar_neither, stop=95.0, target=100.0)
    assert res_neither.price is None
    assert res_neither.reason is None
    assert bool(res_neither) is False

    # Tuple unpacking
    price, reason = resolve_exit(bar_target, stop=95.0, target=100.0)
    assert price == 100.0
    assert reason == "target"


def test_trade_and_session_result_and_protocol():
    """Trade & SessionResult frozen dataclasses and IntradayRules protocol conformance."""
    entry_time = pd.Timestamp("2026-03-06 14:45:00+00:00")
    exit_time = pd.Timestamp("2026-03-06 15:30:00+00:00")
    trade = Trade(
        entry_ts=entry_time,
        entry=100.0,
        stop=98.0,
        target=104.0,
        exit_ts=exit_time,
        exit=104.0,
        exit_reason="target",
        r_multiple=2.0,
    )
    # Frozen check
    with pytest.raises(FrozenInstanceError):
        trade.exit = 105.0  # type: ignore[misc]

    session_res = SessionResult(date=date(2026, 3, 6), trade=trade)
    assert session_res.trade == trade
    assert session_res.date == date(2026, 3, 6)

    # Empty trade in session result
    empty_session = SessionResult(date=date(2026, 3, 6))
    assert empty_session.trade is None

    # Protocol conformance
    class DummyRule:
        def simulate_session(self, bars: pd.DataFrame) -> SessionResult:
            return SessionResult(date=date(2026, 3, 6), trade=trade)

    rule = DummyRule()
    assert isinstance(rule, IntradayRules)
    sim_out = simulate_session(pd.DataFrame(), rule)
    assert sim_out.trade == trade


def test_no_look_ahead():
    """Calculations at bar k never depend on bar k+1."""
    df1 = pd.DataFrame(
        {
            "high": [10.0, 12.0],
            "low": [8.0, 10.0],
            "close": [9.0, 11.0],
            "volume": [100.0, 200.0],
        }
    )
    df2 = pd.DataFrame(
        {
            "high": [10.0, 50.0],  # Bar 1 changed drastically
            "low": [8.0, 40.0],
            "close": [9.0, 45.0],
            "volume": [100.0, 50000.0],
        }
    )
    vwap1 = session_vwap(df1)
    vwap2 = session_vwap(df2)
    # Bar 0 VWAP must be identical despite bar 1 changing completely
    assert np.isclose(vwap1.iloc[0], vwap2.iloc[0])
