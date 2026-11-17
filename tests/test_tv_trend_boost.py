"""Unit tests for TradingView trend boost utilities (Step S1 of PLAN-trendboost.md).

Acceptance criteria:
- inv-vol weights sum <= gross_cap and per-ticker <= cap
- no lookahead (truncating future data does not change past weights/flags)
- defensive rotation never holds risk asset and defensive beyond gross_cap
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils.tv_trend_boost import (
    cash_yield_returns,
    defensive_rotation,
    inv_vol_weights,
    multi_speed_flags,
    soft_flags,
    top_k_by_strength,
)


@pytest.fixture
def sample_market_data() -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex, pd.DataFrame]:
    """Generate 180 bars of reproducible synthetic daily OHLCV data for multiple tickers."""
    np.random.seed(42)
    n = 180
    dates = pd.date_range("2021-01-01", periods=n, freq="D")
    tickers = ["SPY", "QQQ", "IWM", "TLT", "IEF", "GLD", "SHV"]
    dfs: dict[str, pd.DataFrame] = {}
    closes: dict[str, pd.Series] = {}

    for i, t in enumerate(tickers):
        trend = 0.05 * np.sin(np.linspace(0, 3 * np.pi, n) + i * 0.5)
        walk = np.cumsum(np.random.randn(n) * 0.01 + trend * 0.1)
        base = 100.0 * np.exp(walk)
        high = base * (1.0 + np.abs(np.random.randn(n) * 0.01))
        low = base * (1.0 - np.abs(np.random.randn(n) * 0.01))
        open_p = (high + low) / 2.0
        vol = np.random.uniform(1e4, 5e4, n)
        df = pd.DataFrame(
            {"open": open_p, "high": high, "low": low, "close": base, "volume": vol},
            index=dates,
        )
        dfs[t] = df
        closes[t] = pd.Series(base, index=dates)

    closes_df = pd.DataFrame(closes, index=dates)
    return dfs, dates, closes_df


# --- inv_vol_weights tests ----------------------------------------------------


def test_inv_vol_weights_caps_and_sums(sample_market_data):
    """Test that inv-vol weights strictly obey per-ticker and gross caps."""
    _, dates, closes = sample_market_data
    tickers = closes.columns.tolist()

    # All tickers active (flags = True)
    flags_df = pd.DataFrame(True, index=dates, columns=tickers)

    per_ticker_cap = 0.20
    gross_cap = 1.0
    weights = inv_vol_weights(
        flags_df,
        closes,
        target_vol=0.10,
        window=30,
        per_ticker_cap=per_ticker_cap,
        gross_cap=gross_cap,
    )

    assert isinstance(weights, pd.DataFrame)
    assert weights.shape == flags_df.shape

    # Per-ticker cap check: all weights <= per_ticker_cap + 1e-9
    assert (weights <= per_ticker_cap + 1e-9).all().all()

    # Gross cap check: sum of weights across tickers <= gross_cap + 1e-9
    row_sums = weights.sum(axis=1)
    assert (row_sums <= gross_cap + 1e-9).all()

    # Test with tighter gross_cap
    tight_gross = 0.50
    weights_tight = inv_vol_weights(
        flags_df,
        closes,
        target_vol=0.15,
        window=30,
        per_ticker_cap=0.20,
        gross_cap=tight_gross,
    )
    assert (weights_tight.sum(axis=1) <= tight_gross + 1e-9).all()
    assert (weights_tight <= 0.20 + 1e-9).all().all()


def test_inv_vol_weights_insufficient_history(sample_market_data):
    """Test that warmup bars with insufficient rolling history receive 0.0 weight cleanly."""
    _, dates, closes = sample_market_data
    flags_df = pd.DataFrame(True, index=dates, columns=closes.columns)

    window = 60
    weights = inv_vol_weights(flags_df, closes, window=window)

    # First window bars have insufficient history, so weight must be 0.0
    warmup_weights = weights.iloc[: window - 1]
    assert (warmup_weights == 0.0).all().all()

    # Post-warmup bars have positive weights for active flags
    assert (weights.iloc[window:] >= 0.0).all().all()
    assert (weights.iloc[window:].sum(axis=1) > 0.0).any()


def test_inv_vol_weights_no_lookahead(sample_market_data):
    """Verify causality: future data truncation does not alter past weights."""
    _, dates, closes = sample_market_data
    flags_df = pd.DataFrame(True, index=dates, columns=closes.columns)

    window = 40
    full_weights = inv_vol_weights(flags_df, closes, window=window)

    for cutoff in (80, 120, 150):
        trunc_flags = flags_df.iloc[:cutoff]
        trunc_closes = closes.iloc[:cutoff]
        trunc_weights = inv_vol_weights(trunc_flags, trunc_closes, window=window)

        pd.testing.assert_frame_equal(
            full_weights.iloc[:cutoff],
            trunc_weights,
            check_exact=True,
        )


# --- multi_speed_flags tests --------------------------------------------------


def test_multi_speed_flags_mean_range(sample_market_data):
    """Test multi_speed_flags returns arithmetic mean in [0.0, 1.0]."""
    dfs, dates, _ = sample_market_data
    param_sets = [
        {"period": 7, "mult": 2.0},
        {"period": 10, "mult": 3.0},
        {"period": 14, "mult": 4.0},
    ]

    flags = multi_speed_flags(
        dfs,
        dates,
        kind="supertrend",
        param_sets=param_sets,
        universe=["SPY", "QQQ"],
    )

    assert isinstance(flags, pd.DataFrame)
    assert flags.shape == (len(dates), 2)
    assert (flags >= 0.0).all().all()
    assert (flags <= 1.0).all().all()


def test_multi_speed_flags_no_lookahead(sample_market_data):
    """Verify causality of multi_speed_flags."""
    dfs, dates, _ = sample_market_data
    param_sets = [
        {"period": 7, "mult": 2.0},
        {"period": 10, "mult": 3.0},
    ]

    full_flags = multi_speed_flags(
        dfs,
        dates,
        kind="supertrend",
        param_sets=param_sets,
        universe=["SPY"],
    )

    for cutoff in (70, 110):
        trunc_dfs = {t: df.iloc[:cutoff] for t, df in dfs.items()}
        trunc_dates = dates[:cutoff]
        trunc_flags = multi_speed_flags(
            trunc_dfs,
            trunc_dates,
            kind="supertrend",
            param_sets=param_sets,
            universe=["SPY"],
        )
        pd.testing.assert_frame_equal(
            full_flags.iloc[:cutoff],
            trunc_flags,
            check_exact=True,
        )


# --- soft_flags tests ---------------------------------------------------------


def test_soft_flags_continuous_and_capped(sample_market_data):
    """Test soft_flags produces continuous values in [0.0, 1.0] and 0 when inactive."""
    dfs, dates, _ = sample_market_data

    soft = soft_flags(
        dfs,
        dates,
        kind="supertrend",
        params={"period": 10, "mult": 3.0},
        universe=["SPY", "QQQ"],
        max_atr_dist=3.0,
    )

    assert isinstance(soft, pd.DataFrame)
    assert soft.shape == (len(dates), 2)
    assert (soft >= 0.0).all().all()
    assert (soft <= 1.0).all().all()


def test_soft_flags_no_lookahead(sample_market_data):
    """Verify causality of soft_flags."""
    dfs, dates, _ = sample_market_data

    full_soft = soft_flags(
        dfs,
        dates,
        kind="supertrend",
        params={"period": 10, "mult": 3.0},
        universe=["SPY"],
        max_atr_dist=2.5,
    )

    for cutoff in (80, 130):
        trunc_dfs = {t: df.iloc[:cutoff] for t, df in dfs.items()}
        trunc_dates = dates[:cutoff]
        trunc_soft = soft_flags(
            trunc_dfs,
            trunc_dates,
            kind="supertrend",
            params={"period": 10, "mult": 3.0},
            universe=["SPY"],
            max_atr_dist=2.5,
        )
        pd.testing.assert_frame_equal(
            full_soft.iloc[:cutoff],
            trunc_soft,
            check_exact=True,
        )


# --- defensive_rotation tests -------------------------------------------------


def test_defensive_rotation_caps_and_exclusivity(sample_market_data):
    """Test defensive rotation never holds risk asset and defensive beyond gross_cap."""
    _, dates, _ = sample_market_data
    risk_tickers = ["SPY", "QQQ", "IWM"]
    def_tickers = ("TLT", "IEF", "GLD", "SHV")
    all_tickers = risk_tickers + list(def_tickers)

    # Scenario 1: All risk assets active (1.0 weight each, raw flag)
    flags_full_risk = pd.DataFrame(True, index=dates, columns=all_tickers)
    # Defensives also have flags True
    rot_weights_1 = defensive_rotation(
        flags_full_risk,
        defensive=def_tickers,
        gross_cap=1.0,
        per_ticker_cap=0.20,
    )

    # Total across risk + defensive must not exceed gross_cap
    assert (rot_weights_1.sum(axis=1) <= 1.0 + 1e-9).all()
    assert (rot_weights_1 <= 0.20 + 1e-9).all().all()

    # Scenario 2: Risk assets completely off (False), defensives active (True)
    flags_no_risk = pd.DataFrame(False, index=dates, columns=all_tickers)
    for d in def_tickers:
        flags_no_risk[d] = True

    rot_weights_2 = defensive_rotation(
        flags_no_risk,
        defensive=def_tickers,
        gross_cap=1.0,
        per_ticker_cap=0.20,
    )

    # Risk assets get 0.0
    for r in risk_tickers:
        assert (rot_weights_2[r] == 0.0).all()

    # Defensives receive allocation (each <= per_ticker_cap, sum <= gross_cap)
    assert (rot_weights_2.sum(axis=1) <= 1.0 + 1e-9).all()
    assert (rot_weights_2[list(def_tickers)].sum(axis=1) > 0.0).all()

    # Scenario 3: Pre-computed weights where risk assets take 0.90 of 1.0
    custom_weights = pd.DataFrame(0.0, index=dates, columns=risk_tickers)
    custom_weights["SPY"] = 0.20
    custom_weights["QQQ"] = 0.20
    custom_weights["IWM"] = 0.20
    # Total risk is 0.60, unallocated is 0.40

    rot_weights_3 = defensive_rotation(
        flags_full_risk,
        defensive=def_tickers,
        gross_cap=1.0,
        per_ticker_cap=0.20,
        weights_df=custom_weights,
    )

    # Total must never exceed gross_cap
    row_sums = rot_weights_3.sum(axis=1)
    assert (row_sums <= 1.0 + 1e-9).all()
    # Risk weights preserved
    assert np.isclose(rot_weights_3["SPY"], 0.20).all()
    # Defensive gets exactly unallocated (0.40 across 4 defensives = 0.10 each)
    for d in def_tickers:
        assert np.isclose(rot_weights_3[d], 0.10).all()


def test_defensive_rotation_no_lookahead(sample_market_data):
    """Verify causality of defensive_rotation."""
    _, dates, _ = sample_market_data
    tickers = ["SPY", "QQQ", "TLT", "IEF"]
    flags_df = pd.DataFrame(True, index=dates, columns=tickers)

    full_weights = defensive_rotation(
        flags_df,
        defensive=("TLT", "IEF"),
        gross_cap=1.0,
        per_ticker_cap=0.20,
    )

    for cutoff in (75, 125):
        trunc_flags = flags_df.iloc[:cutoff]
        trunc_weights = defensive_rotation(
            trunc_flags,
            defensive=("TLT", "IEF"),
            gross_cap=1.0,
            per_ticker_cap=0.20,
        )
        pd.testing.assert_frame_equal(
            full_weights.iloc[:cutoff],
            trunc_weights,
            check_exact=True,
        )


# --- top_k_by_strength tests --------------------------------------------------


def test_top_k_by_strength(sample_market_data):
    """Test top_k_by_strength limits active tickers to top k by cross-sectional rank."""
    _, dates, _ = sample_market_data
    tickers = ["A", "B", "C", "D", "E"]

    # All active
    flags_df = pd.DataFrame(True, index=dates, columns=tickers)

    # Strength: A=10, B=20, C=30, D=40, E=50 (E and D highest)
    strength_vals = pd.DataFrame(
        {"A": 10.0, "B": 20.0, "C": 30.0, "D": 40.0, "E": 50.0},
        index=dates,
    )

    filtered = top_k_by_strength(flags_df, strength_vals, k=2)

    # At each date, only E and D should be True
    assert (filtered["E"] == True).all()  # noqa: E712
    assert (filtered["D"] == True).all()  # noqa: E712
    assert (filtered["C"] == False).all()  # noqa: E712
    assert (filtered["B"] == False).all()  # noqa: E712
    assert (filtered["A"] == False).all()  # noqa: E712
    assert (filtered.sum(axis=1) == 2).all()

    # If some are inactive, only top k among active are kept
    flags_subset = flags_df.copy()
    flags_subset["E"] = False  # E inactive
    filtered_subset = top_k_by_strength(flags_subset, strength_vals, k=2)

    # Now D and C should be True
    assert (filtered_subset["D"] == True).all()  # noqa: E712
    assert (filtered_subset["C"] == True).all()  # noqa: E712
    assert (filtered_subset["E"] == False).all()  # noqa: E712


# --- cash_yield_returns tests -------------------------------------------------


def test_cash_yield_returns_pre_2007_and_timing():
    """Test cash_yield_returns timing and pre-2007 zero yield rule."""
    dates = pd.date_range("2005-01-01", "2008-01-10", freq="D")
    tickers = ["SPY"]

    # 50% equity weight, 50% cash
    weights_df = pd.DataFrame(0.50, index=dates, columns=tickers)
    # Flat equity prices (0 return from equity)
    closes_df = pd.DataFrame(100.0, index=dates, columns=tickers)

    # SHV close growing 5% per year
    days = len(dates)
    shv_close = pd.Series(100.0 * (1.0 + 0.05 * np.linspace(0, 3, days)), index=dates)

    rets = cash_yield_returns(weights_df, closes_df, shv_close, cost_bp=0.0)

    # Pre-2007: cash yield must be strictly 0.0
    pre_2007_mask = dates.year < 2007
    assert (rets.loc[pre_2007_mask] == 0.0).all()

    # 2007 onwards: cash yield must be positive (50% unallocated * positive SHV return)
    post_2007_mask = (dates.year >= 2007) & (dates > "2007-01-02")
    assert (rets.loc[post_2007_mask] > 0.0).all()


def test_cash_yield_returns_full_allocation():
    """Test that 100% equity allocation receives 0 cash yield."""
    dates = pd.date_range("2010-01-01", periods=50, freq="D")
    weights_df = pd.DataFrame(1.0, index=dates, columns=["SPY"])
    closes_df = pd.DataFrame(100.0, index=dates, columns=["SPY"])
    shv_close = pd.Series(100.0 + np.arange(len(dates)), index=dates)

    rets = cash_yield_returns(weights_df, closes_df, shv_close, cost_bp=0.0)
    # Cash weight is (1.0 - 1.0) = 0.0, so returns are 0.0
    assert (rets.iloc[1:] == 0.0).all()
