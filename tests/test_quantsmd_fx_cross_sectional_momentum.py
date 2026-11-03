import numpy as np
import pandas as pd

from tradingbot.quantsmd_fx_cross_sectional_momentum import (
    QuantsmdFxCrossSectionalMomentumBot,
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


def _exp_frame(end: str, rates: list[float], periods: int = 300) -> pd.DataFrame:
    closes = 100.0 * np.exp(np.outer(np.arange(periods), rates))
    return pd.DataFrame(closes, index=_index(end, periods), columns=FX4)


def test_best_pair_gets_half_of_nav_and_others_are_not_held():
    # Deterministic growth, increasing with column position, so the last column
    # has the highest trailing 63-bar return.
    rates = [0.0001, 0.0002, 0.0003, 0.0010]
    closes = _exp_frame(MONTH_FIRST_END, rates)
    weights = compute_weights(closes)
    assert weights == {FX4[-1]: 0.5}


def test_best_pair_is_taken_even_when_the_best_return_is_negative():
    # Spec: the bot always holds the single best score, with no positive filter.
    rates = [-0.004, -0.003, -0.002, -0.001]
    closes = _exp_frame(MONTH_FIRST_END, rates)
    weights = compute_weights(closes)
    assert weights == {FX4[-1]: 0.5}


def test_fewer_than_four_valid_pairs_returns_empty():
    idx = _index(MONTH_FIRST_END, 300)
    closes = pd.DataFrame(index=idx, columns=FX4, dtype=float)
    closes["EURUSD=X"] = 100.0 * np.exp(np.arange(300) * 0.001)
    closes["GBPUSD=X"] = 100.0 * np.exp(np.arange(300) * 0.0005)
    closes["AUDUSD=X"] = 100.0 * np.exp(np.arange(300) * 0.0002)
    closes["NZDUSD=X"] = np.nan
    assert compute_weights(closes) == {}


def test_mid_month_bar_returns_none():
    closes = _random_walk(MID_MONTH_END, 300)
    assert compute_weights(closes) is None


def test_generic_weights_are_nonnegative_and_sum_at_most_one():
    closes = _random_walk(MONTH_FIRST_END, 600, seed=7)
    weights = compute_weights(closes)
    assert weights is not None
    assert all(w >= 0.0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights
    assert set(weights) <= set(FX4)


def test_generic_too_short_history_returns_empty_or_none():
    closes = _random_walk(MONTH_FIRST_END, 10)
    result = compute_weights(closes)
    assert result is None or result == {}


def test_generic_bot_class_metadata_is_readable():
    assert QuantsmdFxCrossSectionalMomentumBot.BACKTEST_PERIOD == "max"
