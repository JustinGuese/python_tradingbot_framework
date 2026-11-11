"""Tests for option-implied signals calculation, ranking, and bot orchestration.

All tests use synthetic in-memory data with zero DB dependencies.
"""

import datetime
import numpy as np
import pandas as pd
import pytest

from tradingbot.optionimpliedsignalbot import (
    DEFAULT_UNIVERSE,
    ImpliedSignalRules,
    OptionImpliedSignalBot,
    RULES,
)
from tradingbot.utils.implied_signal import (
    compute_cross_sectional_signals,
    compute_portfolio_weights,
    compute_snapshot_signals,
)


def _make_synthetic_chain(
    underlying: str,
    snapshot_at: datetime.datetime,
    spot: float,
    call_iv: float,
    put_iv: float,
    skew: float = 0.02,
) -> pd.DataFrame:
    """Helper to generate a clean synthetic chain with ATM and 25-delta strikes."""
    exp = snapshot_at + datetime.timedelta(days=30)
    strikes = [spot * 0.90, spot * 0.95, spot, spot * 1.05, spot * 1.10]
    rows = []
    for k in strikes:
        # Calls
        c_iv = call_iv if k == spot else (call_iv + 0.01 if k > spot else call_iv - 0.01)
        rows.append(
            {
                "underlying": underlying,
                "contract_symbol": f"{underlying}_{exp:%y%m%d}C{int(k)}",
                "snapshot_at": snapshot_at,
                "expiration": exp,
                "option_type": "C",
                "strike": k,
                "bid": 2.0,
                "ask": 2.2,
                "implied_volatility": c_iv,
                "underlying_price": spot,
            }
        )
        # Puts
        p_iv = put_iv if k == spot else (put_iv + skew if k < spot else put_iv - 0.01)
        rows.append(
            {
                "underlying": underlying,
                "contract_symbol": f"{underlying}_{exp:%y%m%d}P{int(k)}",
                "snapshot_at": snapshot_at,
                "expiration": exp,
                "option_type": "P",
                "strike": k,
                "bid": 2.0,
                "ask": 2.2,
                "implied_volatility": p_iv,
                "underlying_price": spot,
            }
        )
    return pd.DataFrame(rows)


def test_signal_computation_synthetic():
    snap = datetime.datetime(2026, 1, 9, 21, 0)
    df = _make_synthetic_chain("AAPL", snap, spot=100.0, call_iv=0.30, put_iv=0.25, skew=0.04)
    sigs = compute_snapshot_signals(df, target_dte=30)

    assert len(sigs) == 1
    row = sigs.iloc[0]
    assert row["underlying"] == "AAPL"
    assert pytest.approx(row["spot"], rel=1e-3) == 100.0
    # cp_spread = call_iv (0.30) - put_iv (0.25) = +0.05
    assert pytest.approx(row["cp_spread"], abs=1e-3) == 0.05
    # put_skew = put_iv_25d (0.25 + 0.04) - atm_iv (0.275) = 0.015
    assert pytest.approx(row["put_skew"], abs=1e-3) == 0.015


def test_ranks_are_cross_sectional_and_shift_invariant():
    """Ranks must be invariant to a constant shift of all names."""
    snap = datetime.datetime(2026, 1, 9, 21, 0)
    data = [
        {"snapshot_at": snap, "underlying": "AAPL", "cp_spread": 0.05, "rr25": 0.02, "put_skew": 0.01},
        {"snapshot_at": snap, "underlying": "MSFT", "cp_spread": 0.01, "rr25": 0.00, "put_skew": 0.02},
        {"snapshot_at": snap, "underlying": "NVDA", "cp_spread": 0.10, "rr25": 0.05, "put_skew": -0.01},
        {"snapshot_at": snap, "underlying": "AMZN", "cp_spread": -0.02, "rr25": -0.01, "put_skew": 0.03},
    ]
    df1 = pd.DataFrame(data)
    ranked1 = compute_cross_sectional_signals(df1, signal_name="cp_spread", smoothing=1)

    # Shift all cp_spread values by a constant (+10.0)
    df2 = df1.copy()
    df2["cp_spread"] = df2["cp_spread"] + 10.0
    ranked2 = compute_cross_sectional_signals(df2, signal_name="cp_spread", smoothing=1)

    r1 = ranked1.set_index("underlying")["rank"]
    r2 = ranked2.set_index("underlying")["rank"]
    pd.testing.assert_series_equal(r1, r2)
    # Highest should be NVDA, then AAPL, MSFT, AMZN
    assert r1["NVDA"] > r1["AAPL"] > r1["MSFT"] > r1["AMZN"]


def test_missing_option_data_excluded_not_zero():
    """A name with missing option data is excluded (NaN rank), not scored zero."""
    snap = datetime.datetime(2026, 1, 9, 21, 0)
    data = [
        {"snapshot_at": snap, "underlying": "AAPL", "cp_spread": 0.02, "rr25": 0.01, "put_skew": 0.01},
        {"snapshot_at": snap, "underlying": "MSFT", "cp_spread": -0.05, "rr25": -0.02, "put_skew": 0.03},
        {"snapshot_at": snap, "underlying": "GOOGL", "cp_spread": np.nan, "rr25": np.nan, "put_skew": np.nan},
    ]
    df = pd.DataFrame(data)
    ranked = compute_cross_sectional_signals(df, signal_name="cp_spread", smoothing=1)
    ranks = ranked.set_index("underlying")["rank"]

    # GOOGL must have NaN rank
    assert np.isnan(ranks["GOOGL"])
    # MSFT should not beat GOOGL just by virtue of GOOGL being zero:
    # MSFT has negative spread (-0.05), AAPL has positive (0.02)
    assert ranks["AAPL"] == 1.0
    assert ranks["MSFT"] == 0.5  # 1 out of 2 valid

    # Ensure weights exclude GOOGL entirely
    weights = compute_portfolio_weights(ranks, top_n=2)
    assert "GOOGL" not in weights or weights["GOOGL"] == 0.0
    assert weights["AAPL"] == 0.5
    assert weights["MSFT"] == 0.5


def test_weights_sum_le_one():
    """Portfolio weights must always sum to <= 1.0."""
    ranks = pd.Series([1.0, 0.8, 0.6, 0.4, 0.2, np.nan], index=["A", "B", "C", "D", "E", "F"])

    # Full top-5
    w5 = compute_portfolio_weights(ranks, top_n=5)
    assert w5.sum() <= 1.0000001
    assert pytest.approx(w5.sum(), abs=1e-5) == 1.0
    assert w5["F"] == 0.0

    # Top-3 when 5 valid
    w3 = compute_portfolio_weights(ranks, top_n=3)
    assert pytest.approx(w3.sum(), abs=1e-5) == 1.0
    assert w3["D"] == 0.0 and w3["E"] == 0.0

    # Thin universe: top_n=5 but only 2 valid names
    thin_ranks = pd.Series([0.9, 0.5, np.nan], index=["A", "B", "C"])
    w_thin = compute_portfolio_weights(thin_ranks, top_n=5)
    # 2 * (1/5) = 0.4 <= 1.0
    assert w_thin.sum() <= 1.0
    assert pytest.approx(w_thin.sum(), abs=1e-5) == 0.4
    assert w_thin["A"] == 0.2
    assert w_thin["B"] == 0.2
    assert w_thin["C"] == 0.0


def test_bot_rules_and_export(monkeypatch):
    """Verify bot class exports RULES, default rules, and calculates weights."""
    assert hasattr(OptionImpliedSignalBot, "RULES")
    rules = OptionImpliedSignalBot.RULES
    assert rules.signal == "composite"
    assert rules.smoothing == 5
    assert rules.top_n == 5
    assert rules.rebalance == "weekly"

    monkeypatch.setattr(OptionImpliedSignalBot, "__init__", lambda self: None)
    bot = OptionImpliedSignalBot()
    bot.rules = rules
    bot.tradeable_universe = ["AAPL", "MSFT", "NVDA"]

    # Synthetic signal test on bot
    snap = datetime.datetime(2026, 1, 9, 21, 0)
    fake_signals = pd.DataFrame(
        [
            {"snapshot_at": snap, "underlying": "AAPL", "cp_spread": 0.05, "rr25": 0.02, "put_skew": 0.01},
            {"snapshot_at": snap, "underlying": "MSFT", "cp_spread": 0.02, "rr25": 0.01, "put_skew": 0.02},
            {"snapshot_at": snap, "underlying": "NVDA", "cp_spread": 0.08, "rr25": 0.03, "put_skew": 0.01},
        ]
    )
    weights = bot.compute_target_weights(signals_df=fake_signals)
    assert isinstance(weights, dict)
    assert sum(weights.values()) <= 1.0
    assert "NVDA" in weights


def test_study_simulation_and_ic_synthetic():
    """Verify study IC table and simulation pipeline on synthetic multi-date data."""
    from scripts.onetime_implied_signal_study import compute_ic_table, run_strategy_simulation

    dates = pd.date_range("2026-01-02", periods=6, freq="W-FRI")
    underlyings = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL"]
    sig_rows = []
    px_dict = {u: [] for u in underlyings}

    for i, d in enumerate(dates):
        for j, u in enumerate(underlyings):
            base_px = 100.0 * (1.0 + 0.02 * j) * (1.0 + 0.01 * i * (1 if j % 2 == 0 else -1))
            px_dict[u].append(base_px)
            sig_rows.append(
                {
                    "snapshot_at": d,
                    "underlying": u,
                    "spot": base_px,
                    "cp_spread": 0.01 * (j + 1) + 0.005 * i,
                    "rr25": 0.005 * (j + 1),
                    "put_skew": -0.002 * (j + 1),
                }
            )

    sig_df = pd.DataFrame(sig_rows)
    px_df = pd.DataFrame(px_dict, index=dates)
    qqq_px = pd.Series([300.0 * (1.0 + 0.005 * i) for i in range(len(dates))], index=dates)

    # 1. Test IC table
    ic_df = compute_ic_table(sig_df, px_df, signal_names=["cp_spread"])
    assert not ic_df.empty
    assert "Mean Rank IC" in ic_df.columns

    # 2. Test strategy simulation
    params = {"signal": "cp_spread", "smoothing": 1, "top_n": 2, "rebalance": "weekly"}
    curve, trades, bench = run_strategy_simulation(params, sig_df, px_df, qqq_px)
    assert len(curve) > 0
    assert trades > 0
    assert len(bench) == len(curve)
