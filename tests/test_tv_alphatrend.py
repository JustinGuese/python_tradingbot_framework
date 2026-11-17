"""Tests for AlphaTrend indicator (Step S1c)."""

from __future__ import annotations

import os

os.environ.setdefault("POSTGRES_URI", "postgresql://ci:ci@localhost:5432/ci")

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils.tv_alphatrend import alphatrend


def _make_sample_df(n: int = 100, zero_volume: bool = False, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-01 09:30", periods=n, freq="1h")
    # Synthetic price series with random returns
    ret = rng.normal(loc=0.0005, scale=0.015, size=n)
    close = 100.0 * np.exp(np.cumsum(ret))
    high = close * (1.0 + rng.uniform(0.001, 0.01, size=n))
    low = close * (1.0 - rng.uniform(0.001, 0.01, size=n))
    open_p = (high + low) / 2.0
    vol = np.zeros(n) if zero_volume else rng.uniform(100.0, 1000.0, size=n)
    return pd.DataFrame(
        {"open": open_p, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )


def test_zero_volume_falls_back_to_rsi():
    """(1) With all-zero volume, auto-picks RSI and does not return NaN-only."""
    df = _make_sample_df(n=50, zero_volume=True)
    res = alphatrend(df, period=14, mult=1.0, use_mfi=None)

    assert (res["momentum_used"] == "rsi").all()
    # Post-warmup bars must not be NaN
    non_nan_count = res["alphatrend"].dropna().shape[0]
    assert non_nan_count > 0, "alphatrend should not return NaN-only with all-zero volume"
    # First 13 bars are warmup NaN
    assert res["alphatrend"].iloc[:13].isna().all()
    assert res["alphatrend"].iloc[13:].notna().all()


def test_monotonicity_invariant_under_momentum():
    """(2) The line never moves down while momentum >= 50 and never up while < 50."""
    df = _make_sample_df(n=200, zero_volume=False, seed=123)
    res = alphatrend(df, period=14, mult=1.0, use_mfi=True)

    # Recompute MFI independently to check the property invariant
    from tradingbot.utils.tv_core import mfi

    mom = mfi(df, 14)

    at = res["alphatrend"]
    at_prev = at.shift(1)

    # Valid bars where both current and previous bar are defined
    valid = at.notna() & at_prev.notna() & mom.notna()

    # When momentum >= 50, line may only raise or stay flat (never move down)
    high_mom = valid & (mom >= 50.0)
    diff_high = at[high_mom] - at_prev[high_mom]
    assert (diff_high >= -1e-12).all(), f"AlphaTrend decreased while momentum >= 50: {diff_high[diff_high < -1e-12]}"

    # When momentum < 50, line may only lower or stay flat (never move up)
    low_mom = valid & (mom < 50.0)
    diff_low = at[low_mom] - at_prev[low_mom]
    assert (diff_low <= 1e-12).all(), f"AlphaTrend increased while momentum < 50: {diff_low[diff_low > 1e-12]}"


def test_buy_fires_on_first_bar_not_consecutive():
    """(3) Buy fires on the first bar alphatrend crosses above lagged and not consecutively."""
    df = _make_sample_df(n=300, seed=777)
    res = alphatrend(df, period=14, mult=1.0)

    buy = res["buy"]
    sell = res["sell"]
    at = res["alphatrend"]
    lagged = res["lagged"]

    # Verify buy and sell are boolean
    assert buy.dtype == bool
    assert sell.dtype == bool

    # Buy and sell can never be True on the same bar
    assert not (buy & sell).any()

    # Buy never fires on consecutive bars
    assert not (buy & buy.shift(1).fillna(False)).any(), "Buy signal should not fire on consecutive bars"

    # Where buy is True, alphatrend must be strictly above lagged, and prev alphatrend <= prev lagged
    buy_indices = np.where(buy.to_numpy())[0]
    assert len(buy_indices) > 0, "Expected at least one buy signal in a 300-bar random walk"

    for idx in buy_indices:
        assert at.iloc[idx] > lagged.iloc[idx]
        assert at.iloc[idx - 1] <= lagged.iloc[idx - 1]


def test_edge_cases_and_validation():
    """Edge cases: empty df, short df, timestamp column handling, and parameter validation."""
    # Empty DataFrame
    empty_df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    empty_res = alphatrend(empty_df)
    assert list(empty_res.columns) == ["alphatrend", "lagged", "buy", "sell", "momentum_used"]
    assert len(empty_res) == 0

    # Short df (fewer bars than warmup period)
    short_df = _make_sample_df(n=5)
    short_res = alphatrend(short_df, period=14)
    assert short_res["alphatrend"].isna().all()
    assert short_res["lagged"].isna().all()
    assert (~short_res["buy"]).all()
    assert (~short_res["sell"]).all()

    # Frame with timestamp column and non-zero volume
    ts_df = _make_sample_df(n=30).reset_index().rename(columns={"index": "timestamp"})
    ts_res = alphatrend(ts_df, period=10)
    assert len(ts_res) == 30
    assert ts_res["momentum_used"].iloc[0] == "mfi"
    assert ts_res["alphatrend"].dropna().shape[0] > 0

    # Invalid parameters
    df = _make_sample_df(n=20)
    with pytest.raises(ValueError, match="period must be positive"):
        alphatrend(df, period=0)

    with pytest.raises(ValueError, match="mult must be positive"):
        alphatrend(df, mult=-1.0)

    with pytest.raises(ValueError, match="use_mfi must be bool or None"):
        alphatrend(df, use_mfi="invalid")  # type: ignore[arg-type]
