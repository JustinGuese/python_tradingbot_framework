"""Tests for tradingbot/quantsmd_vol_target_trend.py (pure compute_weights, synthetic data)."""

import numpy as np
import pandas as pd

from tradingbot.quantsmd_vol_target_trend import (
    CAP,
    ETF8,
    QuantsmdVolTargetTrendBot,
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
    cols = {}
    for t in drifts:
        sigma = sigmas[t] if isinstance(sigmas, dict) else sigmas
        rets = rng.normal(drifts[t], sigma, size=n)
        cols[t] = 100.0 * np.exp(np.cumsum(rets))
    return pd.DataFrame(cols, index=_index(n, end))


def test_calm_uptrends_get_base_weights_unscaled():
    up = ["SPY", "EFA", "EEM"]
    drifts = {t: (0.002 if t in up else -0.001) for t in ETF8}
    closes = _closes(drifts, 0.002, seed=1)
    weights = compute_weights(closes)
    assert weights is not None
    assert set(weights) == set(up)
    # Calm book sits well under the 10% vol target, so scale is 1.0 and the
    # base equal weight 1/3 (below the 34% cap) is kept.
    for w in weights.values():
        assert abs(w - 1.0 / 3.0) < 1e-9
        assert w <= CAP + 1e-12


def test_noisy_uptrends_are_scaled_below_base_sum():
    # All eight names trend up with 5% daily noise: the basket is far above the
    # 10% vol target, so the vol scale binds and the book sums below the base 1.0.
    drifts = dict.fromkeys(ETF8, 0.01)
    closes = _closes(drifts, 0.05, seed=3)
    weights = compute_weights(closes)
    assert weights
    assert sum(weights.values()) < 1.0 - 0.1


def test_all_downtrending_returns_empty():
    closes = _closes(dict.fromkeys(ETF8, -0.002), 0.005, seed=2)
    assert compute_weights(closes) == {}


def test_mid_month_bar_returns_none():
    closes = _closes(dict.fromkeys(ETF8, 0.002), 0.005, seed=5, end=MID_MONTH_END)
    assert compute_weights(closes) is None


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
    assert set(QuantsmdVolTargetTrendBot.param_grid) == {"fast", "slow", "target_vol"}
    assert QuantsmdVolTargetTrendBot.param_grid["fast"] == [20, 50, 100]
    assert QuantsmdVolTargetTrendBot.param_grid["slow"] == [150, 200, 250]
    assert QuantsmdVolTargetTrendBot.param_grid["target_vol"] == [0.08, 0.10, 0.12]
    assert QuantsmdVolTargetTrendBot.BACKTEST_PERIOD == "max"
