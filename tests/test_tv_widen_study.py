"""Tests for TradingView widen study (Step S5 of PLAN-widen.md).

Only the 4 pre-registered tests specified in PLAN-widen.md S5:
1. split_periods partitions disjointly and fit/validate never contain dates >= 2024-01-01.
2. alpha_stats on synthetic data (ret = 0.5*qqq + const) returns beta ~0.5 and alpha ~ the constant*252.
3. UNIVERSE_EX_EQUITY_WIDE has no duplicates, contains no equity tickers and no "USD".
4. Default (no --holdout) study entry point run_variants(..., holdout=False) on synthetic data
   returns no row with an end date >= 2024-01-01 (small synthetic OHLCV, no network).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.onetime_tv_widen_study import (
    alpha_stats,
    generate_synthetic_study_data,
    run_variants,
    split_periods,
)
from tradingbot.utils.tv_signals import (
    UNIVERSE_EX_EQUITY_WIDE,
)


def test_split_periods_partitions_disjointly_and_fit_validate_sealed() -> None:
    """1. split_periods partitions disjointly and fit/validate never contain dates >= 2024-01-01."""
    idx = pd.date_range("2005-01-01", "2024-12-31", freq="D")
    periods = split_periods(idx)

    fit_idx = periods["fit"]
    val_idx = periods["validate"]
    holdout_idx = periods["holdout"]

    # Disjointness: no date is in more than one partition
    set_fit = set(fit_idx)
    set_val = set(val_idx)
    set_holdout = set(holdout_idx)

    assert set_fit.isdisjoint(set_val), "fit and validate periods must be disjoint"
    assert set_fit.isdisjoint(set_holdout), "fit and holdout periods must be disjoint"
    assert set_val.isdisjoint(set_holdout), "validate and holdout periods must be disjoint"

    # Partition covers the entire original index
    assert len(set_fit) + len(set_val) + len(set_holdout) == len(idx)
    assert (set_fit | set_val | set_holdout) == set(idx)

    # fit/validate never contain dates >= 2024-01-01
    sealed_boundary = pd.Timestamp("2024-01-01")
    assert all(d < sealed_boundary for d in fit_idx), "fit must not contain dates >= 2024-01-01"
    assert all(d < sealed_boundary for d in val_idx), "validate must not contain dates >= 2024-01-01"

    # fit is prior to 2019-01-01
    val_boundary = pd.Timestamp("2019-01-01")
    assert all(d < val_boundary for d in fit_idx), "fit must not contain dates >= 2019-01-01"
    assert all(d >= val_boundary for d in val_idx), "validate must start >= 2019-01-01"
    assert all(d >= sealed_boundary for d in holdout_idx), "holdout must start >= 2024-01-01"


def test_alpha_stats_synthetic() -> None:
    """2. alpha_stats on synthetic data (ret = 0.5*qqq + const) returns beta ~0.5 and alpha ~ the constant*252."""
    rng = np.random.default_rng(12345)
    n = 1000
    dates = pd.date_range("2015-01-01", periods=n, freq="B")

    qqq_ret = pd.Series(rng.normal(0.0005, 0.012, size=n), index=dates)
    const = 0.0004
    ret = 0.5 * qqq_ret + const

    stats = alpha_stats(ret, qqq_ret, n_variants=5)

    assert stats["beta"] == pytest.approx(0.5, abs=1e-6)
    assert stats["alpha"] == pytest.approx(const * 252.0, rel=1e-4, abs=1e-6)
    assert stats["n"] == n


def test_universe_ex_equity_wide_no_duplicates_no_equities_no_usd() -> None:
    """3. UNIVERSE_EX_EQUITY_WIDE has no duplicates, contains no equity tickers and no 'USD'."""
    # No duplicates
    assert len(UNIVERSE_EX_EQUITY_WIDE) == len(set(UNIVERSE_EX_EQUITY_WIDE)), "Must have no duplicate tickers"

    # No 'USD'
    assert "USD" not in UNIVERSE_EX_EQUITY_WIDE, "Must not contain 'USD'"

    # Contains no equity tickers (broad market, sector, individual equities)
    equity_tickers = {
        "SPY",
        "QQQ",
        "IWM",
        "EFA",
        "EEM",
        "XLE",
        "XLF",
        "XLK",
        "XLV",
        "XLP",
        "XLU",
        "XLY",
        "XLI",
        "XLB",
        "XLC",
        "XLRE",
        "VTI",
        "VOO",
        "IVV",
        "VEA",
        "VWO",
        "AAPL",
        "MSFT",
        "AMZN",
        "GOOGL",
        "META",
        "TSLA",
        "NVDA",
    }
    overlap = set(UNIVERSE_EX_EQUITY_WIDE) & equity_tickers
    assert not overlap, f"UNIVERSE_EX_EQUITY_WIDE contains equity tickers: {overlap}"


def test_run_variants_default_holdout_sealed() -> None:
    """4. Default (no --holdout) study entry point run_variants(..., holdout=False) on synthetic data returns no row with an end date >= 2024-01-01 (small synthetic OHLCV, no network)."""
    dfs, master_idx, closes, qqq_ret = generate_synthetic_study_data(
        start="2018-06-01",
        end="2024-06-30",
        seed=42,
    )

    results = run_variants(
        dfs=dfs,
        master_idx=master_idx,
        closes=closes,
        qqq_ret=qqq_ret,
        holdout=False,
    )

    assert not results.empty, "run_variants should return rows for fit/validate"
    assert set(results["period"].unique()).issubset({"fit", "validate"}), (
        f"Without holdout=True, periods must only be fit or validate, got {results['period'].unique()}"
    )

    cutoff = pd.Timestamp("2024-01-01").date()
    for _, row in results.iterrows():
        end_date = pd.to_datetime(row["end_date"]).date()
        assert end_date < cutoff, f"Variant {row['variant']} period {row['period']} has end date {end_date} >= {cutoff}"
