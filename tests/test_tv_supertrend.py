"""Tests for TradingView Supertrend indicator and HTF direction helper."""

from __future__ import annotations

import os

os.environ.setdefault("POSTGRES_URI", "postgresql://ci:ci@localhost:5432/ci")

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils.tv_supertrend import (
    htf_supertrend_direction,
    supertrend,
)


def test_supertrend_monotonic_trends():
    """Direction is +1 in a monotonic uptrend and -1 in a monotonic downtrend.

    Bands ratchet strictly toward price:
    - In an uptrend, lower band never decreases.
    - In a downtrend, upper band never increases.
    """
    dates = pd.date_range("2023-01-01", periods=20, freq="1h")

    # 1. Monotonic uptrend
    df_up = pd.DataFrame(
        {
            "open": [100.0 + i * 2.0 for i in range(20)],
            "high": [101.5 + i * 2.0 for i in range(20)],
            "low": [99.5 + i * 2.0 for i in range(20)],
            "close": [101.0 + i * 2.0 for i in range(20)],
            "volume": [1000.0] * 20,
        },
        index=dates,
    )
    res_up = supertrend(df_up, period=5, mult=2.0)

    assert (res_up["direction"] == 1.0).all()
    # In uptrend, supertrend line should ratchet up (non-decreasing)
    st_up = res_up["supertrend"].to_numpy()
    assert (np.diff(st_up) >= -1e-9).all()
    # First bar has no prior direction to flip from
    assert not res_up["buy"].any()
    assert not res_up["sell"].any()

    # 2. Monotonic downtrend
    df_down = pd.DataFrame(
        {
            "open": [100.0 - i * 2.0 for i in range(20)],
            "high": [100.5 - i * 2.0 for i in range(20)],
            "low": [98.5 - i * 2.0 for i in range(20)],
            "close": [99.0 - i * 2.0 for i in range(20)],
            "volume": [1000.0] * 20,
        },
        index=dates,
    )
    res_down = supertrend(df_down, period=5, mult=2.0)

    assert (res_down["direction"] == -1.0).all()
    # In downtrend, supertrend line should ratchet down (non-increasing)
    st_down = res_down["supertrend"].to_numpy()
    assert (np.diff(st_down) <= 1e-9).all()
    assert not res_down["buy"].any()
    assert not res_down["sell"].any()


def test_supertrend_flips_on_close_through_band():
    """Flips exactly on the bar of a close through the previous bar's band.

    Rising run holds direction +1 through a minor pullback that doesn't breach
    the band; a break below the lower band flips direction to -1 (firing sell);
    a subsequent break above the upper band flips direction to +1 (firing buy).
    """
    dates = pd.date_range("2023-01-01", periods=9, freq="1h")
    # Bars 0-3: steady rise
    # Bar 4: mild pullback that respects lower band (close stays above band)
    # Bar 5: sharp drop closing below previous lower band -> flips to -1, sell=True
    # Bar 6: continuation down -> direction remains -1, sell=False
    # Bar 7: sharp rally closing above previous upper band -> flips to +1, buy=True
    # Bar 8: continuation up -> direction remains +1, buy=False
    opens = [100.0, 102.0, 104.0, 106.0, 107.0, 104.0, 85.0, 88.0, 114.0]
    highs = [103.0, 105.0, 107.0, 109.0, 108.0, 105.0, 86.0, 115.0, 116.0]
    lows = [99.0, 101.0, 103.0, 105.0, 104.0, 84.0, 82.0, 87.0, 112.0]
    closes = [102.0, 104.0, 106.0, 108.0, 105.5, 85.0, 84.0, 113.0, 115.0]

    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes},
        index=dates,
    )
    res = supertrend(df, period=3, mult=2.0)

    # Bars 0-4 are bullish
    assert (res.iloc[0:5]["direction"] == 1.0).all()
    assert not res.iloc[0:5]["buy"].any()
    assert not res.iloc[0:5]["sell"].any()

    # Bar 5: closed below previous lower band -> flips to -1, fires sell
    assert res.iloc[5]["direction"] == -1.0
    assert bool(res.iloc[5]["sell"]) is True
    assert bool(res.iloc[5]["buy"]) is False

    # Bar 6: continues down -> still -1, sell not re-triggered
    assert res.iloc[6]["direction"] == -1.0
    assert bool(res.iloc[6]["sell"]) is False

    # Bar 7: rallied above previous upper band -> flips to +1, fires buy
    assert res.iloc[7]["direction"] == 1.0
    assert bool(res.iloc[7]["buy"]) is True
    assert bool(res.iloc[7]["sell"]) is False

    # Bar 8: continues up -> still +1, buy not re-triggered
    assert res.iloc[8]["direction"] == 1.0
    assert bool(res.iloc[8]["buy"]) is False


def test_htf_supertrend_direction_no_repaint():
    """htf_supertrend_direction uses only completed HTF bars and never repaints.

    Altering later bars leaves all aligned HTF direction values at earlier bars
    strictly unchanged.
    """
    idx = pd.date_range("2023-01-01 00:00", "2023-01-06 00:00", freq="1h")
    n = len(idx)
    df_base = pd.DataFrame(
        {
            "open": [100.0 + i for i in range(n)],
            "high": [101.5 + i for i in range(n)],
            "low": [99.0 + i for i in range(n)],
            "close": [100.5 + i for i in range(n)],
            "volume": [1000.0] * n,
        },
        index=idx,
    )

    htf_dir1 = htf_supertrend_direction(df_base, rule="4h", period=3, mult=2.0)

    # 1. Output is on df_base.index
    assert htf_dir1.index.equals(df_base.index)

    # 2. Bars prior to the first completed 4h bar (00:00-03:00) must be NaN
    assert htf_dir1.loc["2023-01-01 00:00:00":"2023-01-01 03:00:00"].isna().all()

    # 3. Modify future data starting from Jan 3rd 00:00 onwards
    df_modified = df_base.copy()
    df_modified.loc["2023-01-03 00:00":, "close"] -= 500.0
    df_modified.loc["2023-01-03 00:00":, "high"] += 100.0
    df_modified.loc["2023-01-03 00:00":, "low"] -= 600.0

    htf_dir2 = htf_supertrend_direction(df_modified, rule="4h", period=3, mult=2.0)

    # Base bars up to 03:00 on Jan 3rd depend only on 4h bars completed before 00:00 Jan 3rd
    # and must be bit-for-bit identical
    pd.testing.assert_series_equal(
        htf_dir1.loc[:"2023-01-03 03:00"],
        htf_dir2.loc[:"2023-01-03 03:00"],
    )


def test_supertrend_edge_cases_and_validation():
    """Edge cases: empty inputs, validation of parameters and required columns, and NaNs."""
    # 1. Empty DataFrame
    empty_df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    res_empty = supertrend(empty_df)
    assert list(res_empty.columns) == ["supertrend", "direction", "buy", "sell"]
    assert len(res_empty) == 0

    # 2. htf_supertrend_direction on empty DataFrame
    res_htf_empty = htf_supertrend_direction(empty_df, rule="4h")
    assert len(res_htf_empty) == 0

    # 3. Parameter validation
    sample = pd.DataFrame({"high": [10.0], "low": [9.0], "close": [9.5]})
    with pytest.raises(ValueError, match="period must be positive"):
        supertrend(sample, period=0)
    with pytest.raises(ValueError, match="period must be positive"):
        supertrend(sample, period=-5)
    with pytest.raises(ValueError, match="mult must be positive"):
        supertrend(sample, mult=0.0)
    with pytest.raises(ValueError, match="mult must be positive"):
        supertrend(sample, mult=-1.5)

    # 4. Missing required columns
    bad_df = pd.DataFrame({"price": [10.0, 11.0]})
    with pytest.raises(ValueError, match="missing required column"):
        supertrend(bad_df)

    # 5. NaN handling
    dates = pd.date_range("2023-01-01", periods=4, freq="1h")
    nan_df = pd.DataFrame(
        {
            "high": [np.nan, 10.0, 11.0, 12.0],
            "low": [np.nan, 8.0, 9.0, 10.0],
            "close": [np.nan, 9.5, 10.5, 11.5],
        },
        index=dates,
    )
    res_nan = supertrend(nan_df, period=2)
    assert np.isnan(res_nan.iloc[0]["supertrend"])
    assert np.isnan(res_nan.iloc[0]["direction"])
    assert bool(res_nan.iloc[0]["buy"]) is False
    assert bool(res_nan.iloc[0]["sell"]) is False
