"""Tests for the quants.md continuous futures trend bot (pure compute_weights)."""

import numpy as np
import pandas as pd

from tradingbot.quantsmd_continuous_futures_trend import (
    FAST,
    SLOW,
    UNIVERSE,
    VOL_WINDOW,
    QuantsmdContinuousFuturesTrendBot,
    compute_weights,
)


def _frame(
    drifts: dict[str, float], end: str, periods: int = 600, seed: int = 7, noise_sd: float = 0.004
) -> pd.DataFrame:
    """Business-day close frame ending on ``end``; each ticker is a drifted random walk."""
    index = pd.bdate_range(end=end, periods=periods)
    rng = np.random.default_rng(seed)
    data = {}
    for ticker, drift in drifts.items():
        noise = rng.normal(0.0, noise_sd, size=periods)
        data[ticker] = 100.0 * np.exp(np.cumsum(drift + noise))
    return pd.DataFrame(data, index=index)


def _all(drift: float, end: str = "2024-02-01", periods: int = 600) -> pd.DataFrame:
    return _frame(dict.fromkeys(UNIVERSE, drift), end=end, periods=periods)


def test_all_uptrending_on_month_first_bar_is_fully_long():
    closes = _all(0.003)
    weights = compute_weights(closes, FAST, SLOW, VOL_WINDOW)
    assert weights is not None
    assert set(weights) == set(UNIVERSE)
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_all_downtrending_returns_empty():
    closes = _all(-0.003)
    assert compute_weights(closes, FAST, SLOW, VOL_WINDOW) == {}


def test_mid_month_last_bar_returns_none():
    closes = _all(0.003, end="2024-02-15")
    assert compute_weights(closes, FAST, SLOW, VOL_WINDOW) is None


def test_half_up_half_down_keeps_only_uptrends_and_sums_below_one():
    up = UNIVERSE[:3]
    down = UNIVERSE[3:]
    drifts = dict.fromkeys(up, 0.003) | dict.fromkeys(down, -0.003)
    closes = _frame(drifts, end="2024-02-01")
    weights = compute_weights(closes, FAST, SLOW, VOL_WINDOW)
    assert weights is not None
    assert set(weights) <= set(up)
    assert set(weights) == set(up)
    assert 0.0 < sum(weights.values()) < 1.0


def test_generic_weights_are_nonnegative_and_bounded_on_random_walk():
    rng = np.random.default_rng(11)
    index = pd.bdate_range(end="2024-02-01", periods=600)
    closes = pd.DataFrame(
        {t: 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, size=600))) for t in UNIVERSE},
        index=index,
    )
    weights = compute_weights(closes, FAST, SLOW, VOL_WINDOW)
    assert weights is not None
    assert all(w >= 0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights


def test_too_short_history_returns_empty_without_raising():
    closes = _all(0.003, periods=10)
    assert compute_weights(closes, FAST, SLOW, VOL_WINDOW) == {}


def test_bot_class_exposes_param_grid_and_backtest_period():
    assert QuantsmdContinuousFuturesTrendBot.BACKTEST_PERIOD == "max"
    grid = QuantsmdContinuousFuturesTrendBot.param_grid
    assert grid["fast"] == [42, 63, 84]
    assert grid["slow"] == [189, 252]
    assert grid["vol_window"] == [42, 63]
