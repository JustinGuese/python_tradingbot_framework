"""Tests for tradingbot/quantsmd_vol_target_momentum.py (pure compute_weights, synthetic data)."""

import numpy as np
import pandas as pd

from tradingbot.quantsmd_vol_target_momentum import (
    CAP,
    ETF8,
    QuantsmdVolTargetMomentumBot,
    compute_weights,
)

# Thursday 2024-02-01 is a business day whose predecessor (2024-01-31) is in a
# different month, so a frame ending here has a month-first last bar.
MONTH_FIRST_END = "2024-02-01"
MID_MONTH_END = "2024-02-02"


def _index(n: int, end: str = MONTH_FIRST_END) -> pd.DatetimeIndex:
    return pd.bdate_range(end=end, periods=n)


def _closes(
    drifts: dict[str, float],
    sigmas: dict[str, float] | float,
    n: int = 300,
    seed: int = 0,
    end: str = MONTH_FIRST_END,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    tickers = list(drifts)
    cols = {}
    for t in tickers:
        sigma = sigmas[t] if isinstance(sigmas, dict) else sigmas
        rets = rng.normal(drifts[t], sigma, size=n)
        cols[t] = 100.0 * np.exp(np.cumsum(rets))
    return pd.DataFrame(cols, index=_index(n, end))


def _eight(up: list[str], up_drift=0.002, down_drift=-0.001, sigma=0.005, seed=1) -> pd.DataFrame:
    drifts = {t: (up_drift if t in up else down_drift) for t in ETF8}
    return _closes(drifts, sigma, seed=seed)


def test_specific_top_names_are_the_rising_ones():
    up = ["SPY", "EFA", "EEM"]
    closes = _eight(up)
    weights = compute_weights(closes)
    assert weights is not None
    assert set(weights) == set(up)


def test_all_falling_returns_empty():
    closes = _closes(dict.fromkeys(ETF8, -0.002), 0.005, seed=2)
    assert compute_weights(closes) == {}


def test_high_vol_picks_are_scaled_below_base_sum():
    # Strong positive drift keeps momentum positive; 5% daily sigma is far above
    # the 15% annual vol target, so the vol scale binds.
    high = _closes(dict.fromkeys(ETF8, 0.01), 0.05, seed=3)
    weights = compute_weights(high)
    assert weights
    base_sum = 3 * (1.0 / 3.0)
    assert sum(weights.values()) < base_sum - 0.1

    # Calm picks sit well under the target, so the scale is 1 and the base sum holds.
    calm = _closes(dict.fromkeys(ETF8, 0.002), 0.002, seed=4)
    calm_weights = compute_weights(calm)
    assert calm_weights
    assert abs(sum(calm_weights.values()) - base_sum) < 1e-9


def test_mid_month_bar_returns_none():
    closes = _closes(dict.fromkeys(ETF8, 0.002), 0.005, seed=5, end=MID_MONTH_END)
    assert compute_weights(closes) is None


def test_each_weight_capped():
    calm = _closes(dict.fromkeys(ETF8, 0.002), 0.002, seed=6)
    weights = compute_weights(calm, top_n=2)
    assert weights
    assert len(weights) == 2
    for w in weights.values():
        assert w <= CAP + 1e-12
    assert abs(weights[next(iter(weights))] - CAP) < 1e-12


def test_generic_weights_nonnegative_and_bounded_on_random_walk():
    rng = np.random.default_rng(42)
    n = 600
    cols = {t: 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, size=n))) for t in ETF8}
    closes = pd.DataFrame(cols, index=_index(n))
    weights = compute_weights(closes)
    assert weights is not None
    assert all(w >= 0.0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights


def test_generic_too_short_history_returns_empty_without_raising():
    closes = pd.DataFrame(
        {t: np.linspace(100.0, 110.0, 10) for t in ETF8},
        index=_index(10),
    )
    assert compute_weights(closes) == {}


def test_bot_class_exposes_param_grid_and_backtest_period():
    assert set(QuantsmdVolTargetMomentumBot.param_grid) == {"lookback", "top_n", "target_vol"}
    assert QuantsmdVolTargetMomentumBot.param_grid["lookback"] == [63, 126, 189]
    assert QuantsmdVolTargetMomentumBot.BACKTEST_PERIOD == "max"
