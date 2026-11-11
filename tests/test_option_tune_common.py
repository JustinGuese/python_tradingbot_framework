"""Tests for the walk-forward grid tuning harness (scripts/option_tune_common.py)."""

import numpy as np
import pandas as pd
import pytest

from scripts.option_tune_common import (
    WalkForwardDataFrame,
    metrics,
    ship_decision,
    summary_table,
    true_cagr,
    walk_forward,
)


def _make_synth_series(returns: list[float], start_date: str = "2020-01-01", base_val: float = 100.0) -> pd.Series:
    """Generate an equity curve series from daily returns with business day index."""
    idx = pd.date_range(start_date, periods=len(returns) + 1, freq="B")
    values = [base_val]
    for r in returns:
        values.append(values[-1] * (1.0 + r))
    return pd.Series(values, index=idx)


def test_ship_decision_picks_winner_when_beating_defaults():
    """H1 winner ships when its H2 t beats defaults on H2."""
    defaults_h2 = {"t": 1.46, "alpha": 0.05}
    winner_h2 = {"t": 2.37, "alpha": 0.12}
    assert ship_decision(defaults_h2, winner_h2) == "winner"

    # Also test with raw float numbers
    assert ship_decision(1.46, 2.37) == "winner"


def test_ship_decision_picks_defaults_when_winner_loses_on_h2():
    """H1 winner rejected when its H2 t is lower or equal to defaults on H2."""
    defaults_h2 = {"t": 1.76, "alpha": 0.08}
    winner_h2 = {"t": 0.85, "alpha": 0.03}
    assert ship_decision(defaults_h2, winner_h2) == "defaults"

    # Tie also picks defaults
    assert ship_decision({"t": 1.5}, {"t": 1.5}) == "defaults"

    # Both negative, winner worse
    assert ship_decision({"t": -0.2}, {"t": -0.5}) == "defaults"


def test_empty_or_too_short_h2_series_yields_nan_and_never_winner():
    """An empty or too-short H2 series yields NaN t and never ships winner."""
    bench = _make_synth_series([0.001] * 20, start_date="2020-01-01")

    # Empty series
    empty_curve = pd.Series([], dtype=float)
    m_empty = metrics(empty_curve, bench)
    assert np.isnan(m_empty["t"])
    assert np.isnan(m_empty["alpha_t"])

    # Too short series (< 3 points)
    short_curve = pd.Series([100.0, 101.0], index=bench.index[:2])
    m_short = metrics(short_curve, bench)
    assert np.isnan(m_short["t"])

    # When winner has NaN t, ship_decision must never return "winner"
    assert ship_decision({"t": 1.0}, m_empty) == "defaults"
    assert ship_decision({"t": 1.0}, m_short) == "defaults"
    assert ship_decision({"t": np.nan}, m_short) == "defaults"
    assert ship_decision(float("nan"), float("nan")) == "defaults"


def test_walk_forward_grid_runner_functionality():
    """walk_forward runs grid, slices H1/H2, computes metrics, and builds markdown summary."""
    dates = pd.date_range("2020-01-01", periods=100, freq="B")
    bench = pd.Series(100.0 * np.cumprod(1.0 + np.random.RandomState(42).normal(0.0005, 0.01, 100)), index=dates)

    # Combo 1: high alpha in H1, low in H2
    # Combo 2: moderate alpha in H1, high in H2 (winner on H2)
    def synth_run(combo: dict):
        base_ret = np.random.RandomState(combo["seed"]).normal(0.0005, 0.01, 100)
        # Add synthetic excess returns
        if combo["name"] == "combo1":
            base_ret[:50] += 0.003
            base_ret[50:] -= 0.001
        else:
            base_ret[:50] += 0.001
            base_ret[50:] += 0.004
        curve = pd.Series(100.0 * np.cumprod(1.0 + base_ret), index=dates)
        return curve, combo.get("trades", 15)

    grid = [
        {"name": "combo1", "seed": 10, "trades": 20},
        {"name": "combo2", "seed": 20, "trades": 25},
    ]

    split_date = dates[50]
    df = walk_forward(
        grid=grid,
        run_fn=synth_run,
        split=split_date,
        rank_key="alpha_t",
        defaults=grid[0],
        bot_name="option_TestBot",
        benchmark=bench,
        n_jobs=1,
        verbose=False,
    )

    # 1. Structure: one row per combination
    assert isinstance(df, WalkForwardDataFrame)
    assert len(df) == 2

    # 2. Columns: H1/H2/full alpha, t, beta, corr, Sharpe, true_cagr, max DD, trade count
    for prefix in ("H1", "H2", "full"):
        for metric_name in ("alpha", "t", "beta", "corr", "sharpe", "true_cagr", "max_dd", "trades"):
            col = f"{prefix}_{metric_name}"
            assert col in df.columns, f"Missing column: {col}"
            assert np.isfinite(df[col].iloc[0]), f"Non-finite value in {col}"

    assert "trade_count" in df.columns
    assert df["trade_count"].iloc[0] == 20
    assert df["trade_count"].iloc[1] == 25

    # 3. Tuple indexing support on WalkForwardDataFrame
    assert df[("H1", "t")].iloc[0] == df["H1_t"].iloc[0]
    assert df["H1"]["t"].iloc[0] == df["H1_t"].iloc[0]

    # 4. Summary table matching option-bots-round2-2026-09.md columns
    sum_df = df.attrs["summary_df"]
    expected_cols = [
        "Bot",
        "Grid",
        "Defaults, H2 t",
        "H1 winner, H2 t",
        "Top-10 mean H2 t",
        "Grid with H2 t > 0",
        "Shipped",
    ]
    assert list(sum_df.columns) == expected_cols
    assert sum_df["Bot"].iloc[0] == "option_TestBot"
    assert sum_df["Grid"].iloc[0] == 2

    # Markdown export
    sum_md = df.attrs["summary_markdown"]
    assert "| Bot" in sum_md
    assert "| option_TestBot" in sum_md


def test_edge_cases_and_graceful_failures():
    """Handles empty grid and non-finite benchmarks gracefully."""
    empty_df = walk_forward(
        grid=[],
        run_fn=lambda x: None,
        split="2020-01-01",
        bot_name="empty_bot",
        verbose=False,
    )
    assert len(empty_df) == 0
    assert "Bot" in empty_df.attrs["summary_df"].columns

    # Flat / non-varying benchmark
    dates = pd.date_range("2020-01-01", periods=10, freq="B")
    flat_bench = pd.Series([100.0] * 10, index=dates)
    curve = pd.Series(range(100, 110), index=dates, dtype=float)
    m = metrics(curve, flat_bench)
    # With a flat benchmark var(b) == 0, so alpha t is NaN
    assert np.isnan(m["t"])
    assert np.isnan(m["beta"])
    # But CAGR and Sharpe still calculate
    assert np.isfinite(m["true_cagr"])
    assert np.isfinite(m["sharpe"])
