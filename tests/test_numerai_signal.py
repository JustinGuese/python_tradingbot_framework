"""Tests for the pure-function Numerai Signals signal builder."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils.numerai_signal import build_signal


@pytest.fixture
def synthetic_market():
    """Create synthetic price data for 120 tickers over 300 business days."""
    dates = pd.date_range("2023-01-01", periods=300, freq="B", tz="UTC")
    tickers = [f"STOCK_{i:03d}" for i in range(120)]

    # Generate upward/downward drifting prices starting at 100.0
    rng = np.random.default_rng(42)
    daily_returns = rng.normal(0.0005, 0.015, size=(300, 120))
    # Give some stocks distinct drift to verify ranking
    for i in range(120):
        daily_returns[:, i] += (i - 60) * 0.0001

    price_matrix = 100.0 * np.cumprod(1.0 + daily_returns, axis=0)
    prices = pd.DataFrame(price_matrix, index=dates, columns=tickers)
    return prices, tickers, dates


def test_values_strictly_in_0_1_no_nan(synthetic_market):
    """Test 1: Values strictly in (0,1) with no NaN, missing history gets median."""
    prices, tickers, dates = synthetic_market
    asof = dates[280]

    # Universe contains the 120 tickers plus 10 tickers with no data at all
    missing_tickers = [f"MISSING_{i:02d}" for i in range(10)]
    universe = tickers + missing_tickers

    # Invalidate 5 tickers' history (e.g. IPO happened only 50 days ago)
    prices_with_missing = prices.copy()
    prices_with_missing.iloc[:-50, 0:5] = np.nan

    signal = build_signal(prices_with_missing, universe, asof=asof)

    # 1. Output length and index match universe
    assert len(signal) == len(universe)
    assert signal.index.tolist() == universe

    # 2. No NaN values anywhere
    assert not signal.isna().any()

    # 3. All values strictly inside (0, 1) - open interval
    assert (signal > 0.0).all()
    assert (signal < 1.0).all()

    # 4. Tickers with missing history receive the median (0.5)
    for ticker in missing_tickers:
        assert np.isclose(signal[ticker], 0.5)
    for ticker in tickers[0:5]:
        assert np.isclose(signal[ticker], 0.5)

    # 5. Strongest drift stock has higher signal than weakest drift stock
    assert signal[tickers[-1]] > signal[tickers[5]]


def test_no_lookahead_future_price_row(synthetic_market):
    """Test 2: Adding a future price row does not change the signal at asof."""
    prices, tickers, dates = synthetic_market
    asof = dates[260]

    # Signal computed up to asof
    prices_past = prices.loc[:asof].copy()
    signal_past = build_signal(prices_past, tickers, asof=asof)

    # Signal computed with future rows present (including sudden price spike)
    prices_with_future = prices.copy()
    prices_with_future.iloc[265:, :] *= 5.0  # Massive future spike
    signal_with_future = build_signal(prices_with_future, tickers, asof=asof)

    # Must be exactly identical
    pd.testing.assert_series_equal(signal_past, signal_with_future)


def test_under_100_valid_tickers_raises(synthetic_market):
    """Test 3: Fewer than 100 valid tickers raises ValueError (Numerai minimum)."""
    prices, tickers, dates = synthetic_market
    asof = dates[280]

    # Case A: Universe has fewer than 100 tickers total
    small_universe = tickers[:99]
    with pytest.raises(ValueError, match="Fewer than 100 tickers have valid values"):
        build_signal(prices, small_universe, asof=asof)

    # Case B: Universe has 120 tickers, but 25 have missing history (leaving only 95 valid)
    sparse_prices = prices.copy()
    sparse_prices.iloc[:, :25] = np.nan
    with pytest.raises(ValueError, match="Fewer than 100 tickers have valid values"):
        build_signal(sparse_prices, tickers, asof=asof)

    # Case C: History is too short (fewer than 252 bars)
    short_prices = prices.iloc[:150].copy()
    with pytest.raises(ValueError, match="Fewer than 100 tickers have valid values"):
        build_signal(short_prices, tickers, asof=short_prices.index[-1])


def test_security_and_edge_cases(synthetic_market):
    """Edge cases: zero/negative prices, Inf returns, and long-format prices."""
    prices, tickers, dates = synthetic_market
    asof = dates[280]

    # Tickers with 0 or negative prices at start/end should not crash with ZeroDivisionError
    corrupted_prices = prices.copy()
    # At asof = dates[280], start is dates[280 - 252] = dates[28], end is dates[280 - 21] = dates[259]
    corrupted_prices.loc[dates[28], tickers[0]] = 0.0  # division by zero candidate at start
    corrupted_prices.loc[dates[28], tickers[1]] = -10.0  # negative price candidate at start
    corrupted_prices.loc[dates[259], tickers[2]] = np.inf  # inf candidate at end

    # Universe has 120 tickers; 3 are corrupted, leaving 117 valid (> 100)
    signal = build_signal(corrupted_prices, tickers, asof=asof)
    assert not signal.isna().any()
    assert (signal > 0.0).all()
    assert (signal < 1.0).all()
    assert np.isclose(signal[tickers[0]], 0.5)
    assert np.isclose(signal[tickers[1]], 0.5)
    assert np.isclose(signal[tickers[2]], 0.5)

    # Test long-format DataFrame input
    long_subset_tickers = tickers[:105]
    subset_df = prices.loc[:asof, long_subset_tickers].reset_index()
    long_df = pd.melt(subset_df, id_vars=["index"], var_name="symbol", value_name="close")
    long_df.rename(columns={"index": "timestamp"}, inplace=True)

    signal_long = build_signal(long_df, long_subset_tickers, asof=asof)
    assert len(signal_long) == 105
    assert not signal_long.isna().any()
    assert (signal_long > 0.0).all()
    assert (signal_long < 1.0).all()


# --- S3b Cross-Sectional Model Tests (tradingbot.utils.numerai_features) ---


def test_neutralisation_zero_mean_per_group():
    """Test S3b: neutralize removes group means so every group has identically zero mean."""
    from tradingbot.utils.numerai_features import neutralize

    # Case 1: 1D series of group labels (e.g. countries)
    scores = pd.Series(
        [1.2, 2.4, 0.6, 10.0, 14.0, 18.0, -2.0, 3.0],
        index=[f"S{i}" for i in range(8)],
    )
    country_groups = pd.Series(
        ["US", "US", "US", "GB", "GB", "GB", "DE", "DE"],
        index=scores.index,
    )

    neut = neutralize(scores, country_groups)

    # Output has same length and index
    assert len(neut) == len(scores)
    assert neut.index.tolist() == scores.index.tolist()

    # Zero mean per group
    for grp, sub in neut.groupby(country_groups):
        assert np.isclose(sub.mean(), 0.0, atol=1e-10), f"Group {grp} mean is not 0: {sub.mean()}"

    # Overall sum / mean is also zero
    assert np.isclose(neut.mean(), 0.0, atol=1e-10)

    # Case 2: Multi-column DataFrame groups (country + sector)
    df_groups = pd.DataFrame(
        {
            "country": ["US", "US", "US", "US", "GB", "GB", "GB", "GB"],
            "sector": ["TECH", "TECH", "FIN", "FIN", "TECH", "TECH", "FIN", "FIN"],
        },
        index=scores.index,
    )
    neut_df = neutralize(scores, df_groups)
    for (c, s), sub in neut_df.groupby([df_groups["country"], df_groups["sector"]]):
        assert np.isclose(sub.mean(), 0.0, atol=1e-10), f"Group ({c}, {s}) mean is not 0: {sub.mean()}"

    # Case 3: Broad market neutralization when groups is None
    neut_market = neutralize(scores, groups=None)
    assert np.isclose(neut_market.mean(), 0.0, atol=1e-10)


def test_rank_blended_bounds_strictly_in_0_1_no_nan(synthetic_market):
    """Test S3b: build_rank_blended_signal outputs are strictly in (0, 1) with no NaNs."""
    from tradingbot.utils.numerai_features import build_rank_blended_signal

    prices, tickers, dates = synthetic_market
    asof = dates[280]

    # Universe with 120 tickers plus 5 missing tickers
    missing_tickers = [f"MISSING_{i}" for i in range(5)]
    universe = tickers + missing_tickers

    country_map = {t: "US" if i % 2 == 0 else "GB" for i, t in enumerate(tickers)}

    signal = build_rank_blended_signal(
        prices,
        universe,
        asof=asof,
        country_map=country_map,
        min_valid=100,
    )

    # 1. Output length and index match universe
    assert len(signal) == len(universe)
    assert signal.index.tolist() == universe

    # 2. No NaN values anywhere
    assert not signal.isna().any()

    # 3. All values strictly inside (0, 1) - open interval
    assert (signal > 0.0).all()
    assert (signal < 1.0).all()

    # 4. Tickers with missing history receive exactly median (0.5)
    for missing in missing_tickers:
        assert np.isclose(signal[missing], 0.5)


def test_missing_feature_fallback_and_event_lifting(synthetic_market):
    """Test S3b: missing DB data falls back gracefully; present events lift ticker rank."""
    from tradingbot.utils.numerai_features import build_rank_blended_signal

    prices, tickers, dates = synthetic_market
    asof = dates[280]

    # 1. Fallback when buyback and insider events are missing / DB down (default)
    signal_fallback = build_rank_blended_signal(
        prices,
        tickers,
        asof=asof,
        min_valid=100,
    )
    assert not signal_fallback.isna().any()
    assert (signal_fallback > 0.0).all()
    assert (signal_fallback < 1.0).all()

    # 2. When buyback and insider events ARE present, they lift the ticker's rank
    target_ticker = tickers[50]

    # Both stocks have similar return profiles; give target_ticker a buyback and insider buy
    buyback_events = {target_ticker: 1.0}
    insider_trades = {target_ticker: 1.0}

    signal_with_events = build_rank_blended_signal(
        prices,
        tickers,
        asof=asof,
        buyback_events=buyback_events,
        insider_trades=insider_trades,
        min_valid=100,
    )

    # Target ticker score should be higher with positive event flags
    assert signal_with_events[target_ticker] > signal_fallback[target_ticker]


def test_no_shorts_non_negative_weights(synthetic_market):
    """Test S3b: negative weights are strictly forbidden (long-only/flat)."""
    from tradingbot.utils.numerai_features import build_rank_blended_signal

    prices, tickers, dates = synthetic_market
    asof = dates[280]

    # Negative weight must raise ValueError
    with pytest.raises(ValueError, match="must be non-negative"):
        build_rank_blended_signal(
            prices,
            tickers,
            asof=asof,
            weights={"residual_momentum": 0.5, "short_term_reversal": -0.2},
            min_valid=100,
        )
