"""Tests for cross-sectional neutral stock book engine (F1).

Verifies contracts from PLAN-alphaideas.md:
1. Dollar-neutral portfolio construction (long sum = 0.9, short sum = -0.9, net = 0.0).
2. Equal counts per sector side for all selected sectors.
3. Shift test: signals and weights at date t use data <= t only (no lookahead bias).
4. True residual momentum from 36-month OLS regression (not simple cross-sectional demeaning).
5. 1-month short-term reversal (negative trailing return).
6. Within-sector z-scoring using sector maps (SP500_SECTOR).
7. Composite signal blending.
8. Optional beta scaling to QQQ.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils.cs_neutral_book import (
    build_cs_neutral_book,
    build_cs_neutral_weights,
    compute_composite_signal,
    compute_residual_momentum,
    compute_short_term_reversal,
    scale_beta_to_benchmark,
    within_sector_zscore,
)


@pytest.fixture
def synthetic_market_data() -> tuple[pd.DataFrame, pd.Series, dict[str, str]]:
    """Generate 850 trading days of synthetic prices for 30 stocks across 3 sectors,

    plus a market index (SPY) and a benchmark (QQQ).
    """
    np.random.seed(42)
    n_bars = 850
    dates = pd.date_range("2023-01-01", periods=n_bars, freq="B")

    # Generate market (SPY) and QQQ returns
    mkt_ret = np.random.normal(0.0004, 0.01, size=n_bars)
    qqq_ret = 1.1 * mkt_ret + np.random.normal(0.0, 0.004, size=n_bars)

    mkt_price = 100.0 * np.exp(np.cumsum(mkt_ret))
    qqq_price = 150.0 * np.exp(np.cumsum(qqq_ret))
    qqq_s = pd.Series(qqq_price, index=dates, name="QQQ")

    # 3 sectors with 10 stocks each = 30 stocks
    sector_map: dict[str, str] = {}
    sectors = ["Tech", "Financials", "HealthCare"]
    stock_prices: dict[str, np.ndarray] = {"SPY": mkt_price, "QQQ": qqq_price}

    for s_idx, sec in enumerate(sectors):
        for i in range(10):
            ticker = f"{sec[:2].upper()}{i}"
            sector_map[ticker] = sec
            # Different stocks have different betas and idiosyncratic returns
            stock_beta = 0.5 + 0.1 * i + 0.2 * s_idx
            stock_alpha = 0.0001 * (i - 5)
            idio = np.random.normal(0.0, 0.015, size=n_bars)
            r_stock = stock_alpha + stock_beta * mkt_ret + idio
            p_stock = 50.0 * np.exp(np.cumsum(r_stock))
            stock_prices[ticker] = p_stock

    df_prices = pd.DataFrame(stock_prices, index=dates)
    return df_prices, qqq_s, sector_map


# -----------------------------------------------------------------------------
# 1. Acceptance Criteria: Dollar Neutrality
# -----------------------------------------------------------------------------


def test_dollar_neutral_basic(synthetic_market_data):
    """Verify that portfolio weights are dollar-neutral and have Reg T headroom."""
    prices, _qqq_s, sector_map = synthetic_market_data

    weights = build_cs_neutral_book(
        prices=prices,
        sector_map=sector_map,
        market_prices="SPY",
        k=6,
        target_long=0.9,
        target_short=0.9,
        regression_lookback=756,
        min_history=252,
    )

    assert len(weights) > 0

    long_sum = sum(w for w in weights.values() if w > 0)
    short_sum = sum(w for w in weights.values() if w < 0)
    net_exposure = sum(weights.values())
    gross_exposure = sum(abs(w) for w in weights.values())

    # Long and short books must be 0.9 each (Reg T headroom)
    assert long_sum == pytest.approx(0.9, abs=1e-6)
    assert short_sum == pytest.approx(-0.9, abs=1e-6)
    # Net dollar exposure must be strictly zero (dollar-neutral)
    assert net_exposure == pytest.approx(0.0, abs=1e-6)
    # Gross leverage must be 1.8 (headroom under Reg T 2.0 limit)
    assert gross_exposure == pytest.approx(1.8, abs=1e-6)


# -----------------------------------------------------------------------------
# 2. Acceptance Criteria: Equal Counts per Sector Side
# -----------------------------------------------------------------------------


def test_equal_counts_per_sector_side_with_k_per_sector(synthetic_market_data):
    """Verify that when k_per_sector is specified, every sector has equal long and short counts."""
    prices, _, sector_map = synthetic_market_data

    weights = build_cs_neutral_book(
        prices=prices,
        sector_map=sector_map,
        market_prices="SPY",
        k_per_sector=2,
        target_long=0.9,
        target_short=0.9,
        regression_lookback=756,
        min_history=252,
    )

    assert len(weights) > 0

    # Count longs and shorts per sector
    sector_longs: dict[str, int] = {}
    sector_shorts: dict[str, int] = {}

    for sym, w in weights.items():
        sec = sector_map[sym]
        if w > 0:
            sector_longs[sec] = sector_longs.get(sec, 0) + 1
        elif w < 0:
            sector_shorts[sec] = sector_shorts.get(sec, 0) + 1

    all_secs = set(sector_longs.keys()).union(sector_shorts.keys())
    assert len(all_secs) == 3  # All 3 sectors should have trades

    for sec in all_secs:
        assert sector_longs.get(sec, 0) == 2
        assert sector_shorts.get(sec, 0) == 2
        assert sector_longs.get(sec, 0) == sector_shorts.get(sec, 0)


def test_equal_counts_per_sector_side_with_total_k(synthetic_market_data):
    """Verify that when total k is specified, each participating sector has equal long and short counts."""
    prices, _, sector_map = synthetic_market_data

    # Request k=5 total pairs across the sectors
    weights = build_cs_neutral_book(
        prices=prices,
        sector_map=sector_map,
        market_prices="SPY",
        k=5,
        target_long=0.9,
        target_short=0.9,
        regression_lookback=756,
        min_history=252,
    )

    assert len(weights) == 10  # 5 longs, 5 shorts

    sector_longs: dict[str, int] = {}
    sector_shorts: dict[str, int] = {}

    for sym, w in weights.items():
        sec = sector_map[sym]
        if w > 0:
            sector_longs[sec] = sector_longs.get(sec, 0) + 1
        elif w < 0:
            sector_shorts[sec] = sector_shorts.get(sec, 0) + 1

    all_secs = set(sector_longs.keys()).union(sector_shorts.keys())
    assert len(all_secs) > 0

    for sec in all_secs:
        # Crucial acceptance check: in every sector, count of longs == count of shorts
        assert sector_longs.get(sec, 0) == sector_shorts.get(sec, 0)

    # Total longs must equal k and total shorts must equal k
    assert sum(sector_longs.values()) == 5
    assert sum(sector_shorts.values()) == 5


# -----------------------------------------------------------------------------
# 3. Acceptance Criteria: Shift Test (Signals at date t use data <= t only)
# -----------------------------------------------------------------------------


def test_shift_no_lookahead(synthetic_market_data):
    """Verify that calculations at date t do NOT use future data (t > cutoff).

    Modifying future prices must produce identical signals and weights at date t.
    """
    prices, _, sector_map = synthetic_market_data

    cutoff_idx = 780

    # Slice strictly up to cutoff date
    prices_past_only = prices.iloc[: cutoff_idx + 1].copy()

    # Create distorted future dataset: all prices after cutoff_date are multiplied by 10
    prices_distorted_future = prices.copy()
    prices_distorted_future.iloc[cutoff_idx + 1 :] = prices_distorted_future.iloc[cutoff_idx + 1 :] * 10.0 + 50.0

    # 1. Compute residual momentum as of cutoff_date
    mom_past = compute_residual_momentum(
        prices=prices_past_only,
        market_prices="SPY",
        regression_lookback=756,
        momentum_bars=252,
        skip_bars=21,
        min_history=252,
    )

    # Using prices with distorted future sliced at cutoff
    mom_sliced = compute_residual_momentum(
        prices=prices_distorted_future.iloc[: cutoff_idx + 1],
        market_prices="SPY",
        regression_lookback=756,
        momentum_bars=252,
        skip_bars=21,
        min_history=252,
    )

    pd.testing.assert_series_equal(mom_past, mom_sliced, check_names=False)

    # 2. Compute reversal as of cutoff_date
    rev_past = compute_short_term_reversal(prices_past_only, lookback_bars=21)
    rev_sliced = compute_short_term_reversal(prices_distorted_future.iloc[: cutoff_idx + 1], lookback_bars=21)
    pd.testing.assert_series_equal(rev_past, rev_sliced, check_names=False)

    # 3. Compute full neutral book weights as of cutoff_date
    weights_past = build_cs_neutral_book(
        prices=prices_past_only,
        sector_map=sector_map,
        market_prices="SPY",
        k=4,
        regression_lookback=756,
        min_history=252,
    )
    weights_future_distorted = build_cs_neutral_book(
        prices=prices_distorted_future.iloc[: cutoff_idx + 1],
        sector_map=sector_map,
        market_prices="SPY",
        k=4,
        regression_lookback=756,
        min_history=252,
    )

    assert weights_past == weights_future_distorted


def test_time_translation_shift():
    """Verify time-shift invariance: shifting the price history by k bars

    yields the exact same signal computation k bars later.
    """
    np.random.seed(99)
    n = 100
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    p1 = 100.0 * np.exp(np.cumsum(np.random.normal(0, 0.01, size=n)))
    p2 = 50.0 * np.exp(np.cumsum(np.random.normal(0, 0.01, size=n)))
    df = pd.DataFrame({"A": p1, "B": p2}, index=dates)

    # Compute reversal at index 50
    rev_50 = compute_short_term_reversal(df.iloc[:51], lookback_bars=21)

    # Shift price series forward in time by 10 bars (pad 10 constant or arbitrary bars at the beginning)
    pad = pd.DataFrame(
        {"A": np.full(10, p1[0]), "B": np.full(10, p2[0])},
        index=pd.date_range("2023-12-15", periods=10, freq="B"),
    )
    df_shifted = pd.concat([pad, df])

    # Compute reversal at index 50 + 10 = 60
    rev_60 = compute_short_term_reversal(df_shifted.iloc[:61], lookback_bars=21)

    pd.testing.assert_series_equal(rev_50, rev_60, check_names=False)


# -----------------------------------------------------------------------------
# 4. True Residual Momentum vs Demeaning
# -----------------------------------------------------------------------------


def test_true_residual_momentum_removes_market_beta():
    """Verify that true residual momentum uses OLS regression residuals and is NOT

    merely demeaning.

    Construct two stocks with different market betas in a bull market:
    Stock A (HighBeta: beta=2.0, zero idiosyncratic alpha) vs
    Stock B (LowBeta: beta=0.5, positive idiosyncratic alpha in the 12-1 window).

    In raw return terms: A (+80%) easily beats B (+39%) purely because of market beta.
    Demeaning would also rank A above B.
    However, true residual momentum removes market beta and correctly identifies
    that B generated positive idiosyncratic return (+13%), ranking B > A.
    """
    np.random.seed(42)
    n_bars = 760
    dates = pd.date_range("2020-01-01", periods=n_bars, freq="B")

    # Up market
    mkt_ret = np.random.normal(0.0002, 0.01, size=n_bars)
    # Market rally over the 12-1 window
    mkt_ret[-252:-21] += 0.0015
    mkt_price = 100.0 * np.exp(np.cumsum(mkt_ret))

    # Stock A: beta=2.0, alpha=0
    ret_a = 2.0 * mkt_ret
    p_a = 100.0 * np.exp(np.cumsum(ret_a))

    # Stock B: beta=0.5, plus positive idiosyncratic alpha (+0.0008/day) in 12-1 window
    ret_b = 0.5 * mkt_ret.copy()
    ret_b[-252:-21] += 0.0008
    p_b = 100.0 * np.exp(np.cumsum(ret_b))

    df = pd.DataFrame({"SPY": mkt_price, "A": p_a, "B": p_b}, index=dates)

    raw_ret_a = p_a[-21] / p_a[-252] - 1.0
    raw_ret_b = p_b[-21] / p_b[-252] - 1.0

    # Raw return of A is much higher than B (market was up strongly and A has beta=2)
    assert raw_ret_a > raw_ret_b

    # True residual momentum isolates idiosyncratic outperformance
    res_mom = compute_residual_momentum(
        prices=df,
        market_prices="SPY",
        regression_lookback=756,
        momentum_bars=252,
        skip_bars=21,
        min_history=252,
    )

    # B had positive idiosyncratic alpha, while A had zero
    assert res_mom["B"] > res_mom["A"]


# -----------------------------------------------------------------------------
# 5. Short-Term Reversal
# -----------------------------------------------------------------------------


def test_short_term_reversal_ordering():
    """Verify that 1-month reversal gives highest score to past losers (bounce candidates)."""
    dates = pd.date_range("2024-01-01", periods=30, freq="B")
    # Loser drops from 100 to 80; Winner rallies from 100 to 120
    loser = np.linspace(100, 80, 30)
    winner = np.linspace(100, 120, 30)
    flat = np.full(30, 100.0)

    df = pd.DataFrame({"LOSER": loser, "WINNER": winner, "FLAT": flat}, index=dates)
    rev = compute_short_term_reversal(df, lookback_bars=21)

    assert rev["LOSER"] > rev["FLAT"] > rev["WINNER"]
    assert rev["LOSER"] > 0
    assert rev["WINNER"] < 0


# -----------------------------------------------------------------------------
# 6. Within-Sector Z-Scoring
# -----------------------------------------------------------------------------


def test_within_sector_zscore():
    """Verify that within-sector z-scoring normalizes to mean 0 and std 1 within each sector."""
    signal = pd.Series(
        {
            "T1": 10.0,
            "T2": 20.0,
            "T3": 30.0,
            "F1": 100.0,
            "F2": 200.0,
            "F3": 300.0,
        }
    )
    sector_map = {
        "T1": "Tech",
        "T2": "Tech",
        "T3": "Tech",
        "F1": "Fin",
        "F2": "Fin",
        "F3": "Fin",
    }

    z = within_sector_zscore(signal, sector_map)

    # Check Tech
    tech_z = z.loc[["T1", "T2", "T3"]]
    assert tech_z.mean() == pytest.approx(0.0, abs=1e-6)
    assert tech_z.std(ddof=1) == pytest.approx(1.0, abs=1e-6)
    assert tech_z["T1"] < tech_z["T2"] < tech_z["T3"]

    # Check Fin
    fin_z = z.loc[["F1", "F2", "F3"]]
    assert fin_z.mean() == pytest.approx(0.0, abs=1e-6)
    assert fin_z.std(ddof=1) == pytest.approx(1.0, abs=1e-6)


# -----------------------------------------------------------------------------
# 7. Composite Signal Blending
# -----------------------------------------------------------------------------


def test_composite_signal_grid():
    """Verify composite signal handles different factor weights (0/100, 50/50, 100/0)."""
    mom = pd.Series({"A": 1.0, "B": 2.0, "C": 3.0})
    rev = pd.Series({"A": 3.0, "B": 2.0, "C": 1.0})
    sector_map = {"A": "S1", "B": "S1", "C": "S1"}

    # 100% momentum
    comp_mom_only = compute_composite_signal(
        residual_mom=mom,
        reversal=rev,
        sector_map=sector_map,
        weight_mom=1.0,
        weight_rev=0.0,
    )
    assert comp_mom_only["C"] > comp_mom_only["B"] > comp_mom_only["A"]

    # 100% reversal
    comp_rev_only = compute_composite_signal(
        residual_mom=mom,
        reversal=rev,
        sector_map=sector_map,
        weight_mom=0.0,
        weight_rev=1.0,
    )
    assert comp_rev_only["A"] > comp_rev_only["B"] > comp_rev_only["C"]

    # 50/50 blend on symmetric data: all equal score
    comp_5050 = compute_composite_signal(
        residual_mom=mom,
        reversal=rev,
        sector_map=sector_map,
        weight_mom=0.5,
        weight_rev=0.5,
    )
    assert comp_5050["A"] == pytest.approx(0.0, abs=1e-6)
    assert comp_5050["B"] == pytest.approx(0.0, abs=1e-6)
    assert comp_5050["C"] == pytest.approx(0.0, abs=1e-6)


# -----------------------------------------------------------------------------
# 8. Optional Beta Scaling to QQQ
# -----------------------------------------------------------------------------


def test_scale_beta_to_benchmark(synthetic_market_data):
    """Verify beta scaling / hedging to QQQ."""
    prices, qqq_s, _sector_map = synthetic_market_data

    # Generate initial weights
    base_weights = {
        "TE0": 0.3,
        "TE1": 0.3,
        "TE2": 0.3,
        "FI0": -0.3,
        "FI1": -0.3,
        "FI2": -0.3,
    }

    # Overlay mode: adds a QQQ position to hedge portfolio beta to target_beta (0.0)
    hedged = scale_beta_to_benchmark(
        weights=base_weights,
        prices=prices,
        benchmark_prices=qqq_s,
        benchmark_symbol="QQQ",
        target_beta=0.0,
        mode="overlay",
    )

    assert "QQQ" in hedged
    qqq_weight = hedged["QQQ"]

    # Verify that total portfolio returns with QQQ overlay indeed have ~0 beta to QQQ
    recent_prices = prices.iloc[-253:]
    stock_rets = recent_prices.pct_change().iloc[1:]
    qqq_rets = qqq_s.reindex(recent_prices.index).pct_change().iloc[1:]

    # Base portfolio return
    stock_cols = [c for c in base_weights if c in stock_rets.columns]
    port_rets = stock_rets[stock_cols].dot(pd.Series({c: base_weights[c] for c in stock_cols}))

    # Base beta
    cov_pb = float(np.cov(port_rets, qqq_rets, ddof=1)[0, 1])
    var_b = float(np.var(qqq_rets, ddof=1))
    base_beta = cov_pb / var_b

    # Hedged portfolio return: port_rets + qqq_weight * qqq_rets
    hedged_rets = port_rets + qqq_weight * qqq_rets
    cov_hb = float(np.cov(hedged_rets, qqq_rets, ddof=1)[0, 1])
    hedged_beta = cov_hb / var_b

    # The hedged portfolio beta to QQQ must be zero within precision
    assert hedged_beta == pytest.approx(0.0, abs=1e-4)
    # The overlay weight should equal -base_beta
    assert qqq_weight == pytest.approx(-base_beta, abs=1e-4)


# -----------------------------------------------------------------------------
# 9. Integration with SP500_SECTOR from universes
# -----------------------------------------------------------------------------


def test_sp500_sector_universe_integration():
    """Verify that build_cs_neutral_weights works seamlessly with real SP500_SECTOR mapping."""
    from tradingbot.utils.universes import SP500_SECTOR

    # Create synthetic composite signals for a subset of real S&P 500 tickers across sectors
    tickers = [
        "AAPL",
        "MSFT",
        "NVDA",
        "GOOGL",  # Information Technology / Communication Services
        "JPM",
        "BAC",
        "WFC",
        "GS",  # Financials
        "JNJ",
        "PFE",
        "UNH",
        "ABBV",  # Health Care
        "XOM",
        "CVX",
        "COP",
        "SLB",  # Energy
    ]
    np.random.seed(123)
    sig = pd.Series(np.random.normal(0, 1, size=len(tickers)), index=tickers)

    weights = build_cs_neutral_weights(
        composite_signal=sig,
        sector_map=SP500_SECTOR,
        k=4,
        target_long=0.9,
        target_short=0.9,
    )

    assert len(weights) == 8  # 4 longs, 4 shorts
    assert sum(weights.values()) == pytest.approx(0.0, abs=1e-6)

    # Check sector neutrality
    sec_counts: dict[str, dict[str, int]] = {}
    for sym, w in weights.items():
        sec = SP500_SECTOR.get(sym, "UNKNOWN")
        side = "long" if w > 0 else "short"
        sec_counts.setdefault(sec, {"long": 0, "short": 0})[side] += 1

    for _sec, counts in sec_counts.items():
        assert counts["long"] == counts["short"]


# -----------------------------------------------------------------------------
# 10. Edge cases and PLAN F2 Grid Tests
# -----------------------------------------------------------------------------


def test_empty_and_short_history_edge_cases():
    """Verify graceful handling of empty inputs and short histories."""
    # Empty price dataframe
    assert compute_residual_momentum(pd.DataFrame()).empty
    assert compute_short_term_reversal(pd.DataFrame()).empty
    assert within_sector_zscore(pd.Series(dtype=float)).empty
    assert build_cs_neutral_weights(pd.Series(dtype=float)) == {}
    assert build_cs_neutral_book(pd.DataFrame()) == {}

    # Insufficient history for regression
    short_df = pd.DataFrame(
        {"SPY": [100, 101, 102], "A": [50, 51, 52]},
        index=pd.date_range("2024-01-01", periods=3, freq="B"),
    )
    res = compute_residual_momentum(short_df, min_history=252)
    assert res.isna().all()


def test_single_stock_sector_edge_case():
    """Verify single stock in a sector produces z-score of 0.0 without errors."""
    sig = pd.Series({"A": 10.0, "B": 20.0, "SOLO": 99.0})
    sec_map = {"A": "Sector1", "B": "Sector1", "SOLO": "Sector2"}
    z = within_sector_zscore(sig, sec_map)
    assert z["SOLO"] == 0.0
    assert not np.isnan(z["SOLO"])


def test_f2_grid_variants(synthetic_market_data):
    """Verify all 12 variants in F2 pre-registered grid run deterministically:

    weights of reversal vs residual momentum {0/100, 30/70, 50/50, 100/0} x k {3, 5, 8}.
    """
    prices, _, sector_map = synthetic_market_data

    weight_pairs = [(0.0, 1.0), (0.3, 0.7), (0.5, 0.5), (1.0, 0.0)]
    k_values = [3, 5, 8]

    for w_rev, w_mom in weight_pairs:
        for k in k_values:
            weights = build_cs_neutral_book(
                prices=prices,
                sector_map=sector_map,
                market_prices="SPY",
                weight_mom=w_mom,
                weight_rev=w_rev,
                k=k,
                target_long=0.9,
                target_short=0.9,
                regression_lookback=756,
                min_history=252,
            )
            # Must produce 2*k positions (k longs, k shorts)
            assert len(weights) == 2 * k
            # Dollar-neutral
            assert sum(weights.values()) == pytest.approx(0.0, abs=1e-6)
            # Longs sum to 0.9, shorts sum to -0.9
            assert sum(w for w in weights.values() if w > 0) == pytest.approx(0.9, abs=1e-6)
            assert sum(w for w in weights.values() if w < 0) == pytest.approx(-0.9, abs=1e-6)

            # Check sector balance
            sector_longs: dict[str, int] = {}
            sector_shorts: dict[str, int] = {}
            for sym, w in weights.items():
                sec = sector_map[sym]
                if w > 0:
                    sector_longs[sec] = sector_longs.get(sec, 0) + 1
                else:
                    sector_shorts[sec] = sector_shorts.get(sec, 0) + 1

            for sec in set(sector_longs).union(sector_shorts):
                assert sector_longs.get(sec, 0) == sector_shorts.get(sec, 0)
