"""Tests for TradingView Ultimate Moving Average (CM_Ultimate_MA_MTF)."""

from __future__ import annotations

import os

os.environ.setdefault("POSTGRES_URI", "postgresql://ci:ci@localhost:5432/ci")

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils.tv_ultimate_ma import SUPPORTED_KINDS, ultimate_ma


def test_ultimate_ma_kinds_and_validation():
    """Each kind returns a finite last value on a ramp and direction == +1; bad kind raises."""
    idx = pd.date_range("2023-01-01", periods=60, freq="1h")
    # Linear ramp: close values strictly increasing from 1.0 to 60.0
    ramp = pd.Series(np.arange(1.0, 61.0, dtype=float), index=idx)

    for k in SUPPORTED_KINDS:
        res = ultimate_ma(ramp, kind=k, length=10)
        assert isinstance(res, pd.DataFrame)
        assert list(res.columns) == ["ma", "direction", "turn_up", "turn_down"]
        assert res.index.equals(ramp.index)
        # Last MA must be finite
        assert np.isfinite(res["ma"].iloc[-1])
        # Direction on monotonic upward ramp must be +1.0
        assert res["direction"].iloc[-1] == 1.0

    # Unknown kind raises ValueError
    with pytest.raises(ValueError, match="Unknown moving average kind"):
        ultimate_ma(ramp, kind="alma")

    # Invalid length or slope_bars raises ValueError
    with pytest.raises(ValueError, match="length must be positive"):
        ultimate_ma(ramp, length=0)

    with pytest.raises(ValueError, match="slope_bars must be positive"):
        ultimate_ma(ramp, slope_bars=0)


def test_ultimate_ma_turn_flags_v_reversal():
    """Turn flags fire exactly once at a V-shaped reversal."""
    idx = pd.date_range("2023-01-01", periods=60, freq="1h")

    # 1. V-shaped bottom: falls from 30 down to 1, then rises from 2 to 31
    bottom_vals = list(range(30, 0, -1)) + list(range(2, 32))
    s_bottom = pd.Series(bottom_vals, index=idx, dtype=float)

    res_bottom = ultimate_ma(s_bottom, kind="sma", length=5)
    # turn_up must fire exactly once
    assert res_bottom["turn_up"].sum() == 1
    # turn_down must never fire in a pure V-bottom
    assert res_bottom["turn_down"].sum() == 0

    turn_up_idx = res_bottom["turn_up"].idxmax()
    loc = idx.get_loc(turn_up_idx)
    assert res_bottom.loc[turn_up_idx, "direction"] == 1.0
    assert res_bottom["direction"].iloc[loc - 1] == -1.0

    # 2. Inverted V-shaped peak: rises from 1 to 30, then falls from 29 to 0
    top_vals = list(range(1, 31)) + list(range(29, -1, -1))
    s_top = pd.Series(top_vals, index=idx, dtype=float)

    res_top = ultimate_ma(s_top, kind="sma", length=5)
    # turn_down must fire exactly once
    assert res_top["turn_down"].sum() == 1
    # turn_up must never fire in a pure inverted V-top
    assert res_top["turn_up"].sum() == 0

    turn_down_idx = res_top["turn_down"].idxmax()
    loc_top = idx.get_loc(turn_down_idx)
    assert res_top.loc[turn_down_idx, "direction"] == -1.0
    assert res_top["direction"].iloc[loc_top - 1] == 1.0


def test_ultimate_ma_htf_rule_no_lookahead():
    """With htf_rule, earlier output is unchanged when later base bars are modified."""
    idx = pd.date_range("2023-01-01 00:00", periods=72, freq="1h")
    # Base series
    s = pd.Series(np.sin(np.linspace(0, 10, len(idx))) * 10.0 + 100.0, index=idx)

    res_base = ultimate_ma(s, kind="hma", length=4, htf_rule="4h")
    assert isinstance(res_base, pd.DataFrame)
    assert list(res_base.columns) == ["ma", "direction", "turn_up", "turn_down"]

    # Alter future bars from 2023-01-02 12:00 onwards
    s_modified = s.copy()
    s_modified.loc["2023-01-02 12:00:00":] += 500.0

    res_modified = ultimate_ma(s_modified, kind="hma", length=4, htf_rule="4h")

    # In 4h resampling, the bar starting at 12:00 closes at 16:00.
    # Therefore, all output through 15:00 is based solely on completed HTF bars
    # from before 12:00 and must be strictly identical.
    cutoff = "2023-01-02 15:00:00"
    pd.testing.assert_frame_equal(res_base.loc[:cutoff], res_modified.loc[:cutoff])


def test_ultimate_ma_edge_cases_and_security():
    """Test empty input, OHLCV DataFrame input, and timezone/type handling."""
    # 1. Empty series
    empty_s = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    res_empty = ultimate_ma(empty_s)
    assert res_empty.empty
    assert list(res_empty.columns) == ["ma", "direction", "turn_up", "turn_down"]

    # 2. DataFrame input with OHLCV columns and tz-aware index
    idx = pd.date_range("2023-01-01", periods=30, freq="1h", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": np.arange(30, dtype=float),
            "High": np.arange(30, dtype=float) + 1,
            "Low": np.arange(30, dtype=float) - 1,
            "Close": np.arange(30, dtype=float),
            "Volume": np.full(30, 100.0),
        },
        index=idx,
    )
    res_df = ultimate_ma(df, kind="ema", length=5)
    assert len(res_df) == 30
    assert res_df.index.tz is None  # Normalized to tz-naive
    assert res_df["direction"].iloc[-1] == 1.0

    # 3. Non-DatetimeIndex with htf_rule raises ValueError
    s_int = pd.Series(np.arange(20, dtype=float))
    with pytest.raises(ValueError, match="htf_rule requires a DatetimeIndex"):
        ultimate_ma(s_int, htf_rule="4h")
