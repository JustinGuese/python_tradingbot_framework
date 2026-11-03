import numpy as np
import pandas as pd

from tradingbot.quantsmd_fx_time_series_momentum import (
    QuantsmdFxTimeSeriesMomentumBot,
    compute_weights,
)
from tradingbot.utils.quantsmd_common import FX4

# 2024-08-01 is a Thursday and the bar before it (2024-07-31) is in July, so a
# frame ending here has a month-first last bar.
MONTH_FIRST_END = "2024-08-01"
# 2024-07-15 is mid-month, so a frame ending here is not month-first.
MID_MONTH_END = "2024-07-15"


def _index(end: str, periods: int) -> pd.DatetimeIndex:
    return pd.bdate_range(end=end, periods=periods)


def _random_walk(end: str, periods: int, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = _index(end, periods)
    returns = rng.normal(0.0004, 0.01, size=(periods, len(FX4)))
    closes = 100.0 * np.exp(np.cumsum(returns, axis=0))
    return pd.DataFrame(closes, index=idx, columns=FX4)


def _drift_frame(end: str, drifts: list[float], periods: int = 300, seed: int = 3) -> pd.DataFrame:
    """Each column has its own drift and the same noise, so volatility is positive and comparable."""
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, 0.01, size=periods)
    returns = np.column_stack([d + noise for d in drifts])
    closes = 100.0 * np.exp(np.cumsum(returns, axis=0))
    return pd.DataFrame(closes, index=_index(end, periods), columns=FX4)


def test_three_rising_pairs_and_one_falling_drops_the_falling_pair():
    closes = _drift_frame(MONTH_FIRST_END, [0.003, 0.003, 0.003, -0.003])
    weights = compute_weights(closes, lookback=126)
    assert weights is not None
    assert set(weights) == set(FX4[:3])
    assert "EURUSD=X" in weights
    assert "NZDUSD=X" not in weights
    assert sum(weights.values()) < 1.0
    assert all(w > 0.0 for w in weights.values())


def test_all_rising_equal_vol_pairs_get_a_quarter_each():
    closes = _drift_frame(MONTH_FIRST_END, [0.003, 0.003, 0.003, 0.003])
    # Equal drifts and shared noise make every column identical, so sign and
    # volatility match and each raw weight is the same.
    weights = compute_weights(closes, lookback=126)
    assert weights is not None
    assert set(weights) == set(FX4)
    for w in weights.values():
        assert abs(w - 0.25) < 1e-9
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_all_falling_pairs_give_empty_book():
    closes = _drift_frame(MONTH_FIRST_END, [-0.003, -0.003, -0.003, -0.003])
    assert compute_weights(closes, lookback=126) == {}


def test_mid_month_bar_returns_none():
    closes = _random_walk(MID_MONTH_END, 300)
    assert compute_weights(closes, lookback=126) is None


def test_generic_weights_are_nonnegative_and_sum_at_most_one():
    closes = _random_walk(MONTH_FIRST_END, 600, seed=7)
    weights = compute_weights(closes, lookback=126)
    assert weights is not None
    assert all(w >= 0.0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights
    assert set(weights) <= set(FX4)


def test_generic_too_short_history_returns_empty_or_none():
    closes = _random_walk(MONTH_FIRST_END, 10)
    result = compute_weights(closes, lookback=126)
    assert result is None or result == {}


def test_generic_bot_class_metadata_is_readable():
    assert "lookback" in QuantsmdFxTimeSeriesMomentumBot.param_grid
    assert QuantsmdFxTimeSeriesMomentumBot.param_grid["lookback"] == [63, 126, 252]
    assert QuantsmdFxTimeSeriesMomentumBot.BACKTEST_PERIOD == "max"
