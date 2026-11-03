import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_cross_sectional_momentum_v2 import (
    GROSS,
    LOOKBACK,
    N_LONG,
    SKIP,
    UNIVERSE,
    QuantsmdCrossSectionalMomentumV2Bot,
    compute_weights,
)
from tradingbot.utils.quantsmd_common import SECTORS9

# 2024-08-01 is a Thursday and the bar before it (2024-07-31) is in July, so a
# frame ending here has a month-first last bar.
MONTH_FIRST_END = "2024-08-01"
# 2024-07-15 is mid-month, so a frame ending here is not month-first.
MID_MONTH_END = "2024-07-15"
N_ROWS = 300


def _index(end: str, periods: int) -> pd.DatetimeIndex:
    return pd.bdate_range(end=end, periods=periods)


def _frame_from_returns(returns: np.ndarray, end: str = MONTH_FIRST_END) -> pd.DataFrame:
    idx = _index(end, returns.shape[0])
    closes = 100.0 * np.exp(np.cumsum(returns, axis=0))
    return pd.DataFrame(closes, index=idx, columns=UNIVERSE)


def _random_walk(end: str, periods: int, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0004, 0.01, size=(periods, len(UNIVERSE)))
    return _frame_from_returns(returns, end)


def _reversal_frame() -> pd.DataFrame:
    """Drift ranks by column for the first 279 bars, reversed over the last 21.

    The same noise hits every name, so the ranking is set by drift alone and the
    volatility of each name stays close to the others.
    """
    rng = np.random.default_rng(3)
    shared = rng.normal(0.0, 0.05, N_ROWS)
    j = np.arange(len(UNIVERSE))
    drift = np.empty((N_ROWS, len(UNIVERSE)))
    drift[: N_ROWS - SKIP] = 0.0004 * j
    drift[N_ROWS - SKIP :] = -0.006 * j
    returns = drift + shared[:, None]
    return _frame_from_returns(returns)


def test_skip_ignores_the_last_month_and_changes_the_pick():
    closes = _reversal_frame()
    # Without the skip, the 21 reversed bars drag the top three to the front.
    with_recent = compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG, skip=0)
    assert set(with_recent) == set(UNIVERSE[:3])
    # Skipping the last 21 bars picks the names that led before the reversal.
    skipped = compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG, skip=SKIP)
    assert set(skipped) == set(UNIVERSE[-3:])
    for w in skipped.values():
        assert w == pytest.approx(GROSS / N_LONG)


def test_vol_filter_removes_a_noisy_name():
    rng = np.random.default_rng(11)
    shared = rng.normal(0.0, 0.01, N_ROWS)
    j = np.arange(len(UNIVERSE))
    drift = np.tile(0.0004 * j, (N_ROWS, 1))
    returns = drift + shared[:, None]
    # The top momentum name (XLY) also swings up and down by 5% every bar. An
    # alternating swing sums to zero over the even 252-bar momentum window, so its
    # momentum stays top but its volatility is about 5x the median.
    swing = np.where(np.arange(N_ROWS) % 2 == 0, 0.05, -0.05)
    noisy = returns.copy()
    noisy[:, -1] = returns[:, -1] + swing
    assert set(compute_weights(_frame_from_returns(returns), skip=SKIP)) == set(UNIVERSE[-3:])
    filtered = compute_weights(_frame_from_returns(noisy), skip=SKIP)
    assert set(filtered) == set(UNIVERSE[-4:-1])
    assert UNIVERSE[-1] not in filtered


def test_non_month_first_bar_returns_none():
    closes = _random_walk(MID_MONTH_END, N_ROWS)
    assert compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG) is None


def test_fewer_rows_than_lookback_plus_skip_plus_one_returns_empty():
    closes = _random_walk(MONTH_FIRST_END, LOOKBACK + SKIP)
    assert compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG, skip=SKIP) == {}


def test_fewer_than_six_valid_names_returns_empty():
    closes = _random_walk(MONTH_FIRST_END, N_ROWS)
    for t in UNIVERSE[:4]:
        closes[t] = np.nan
    assert compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG, skip=SKIP) == {}


def test_generic_weights_nonnegative_and_sum_at_most_one():
    closes = _random_walk(MONTH_FIRST_END, 600, seed=7)
    weights = compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG, skip=SKIP)
    assert all(w >= 0.0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights
    assert set(weights) <= set(UNIVERSE)


def test_generic_bot_class_metadata_is_readable():
    assert list(UNIVERSE) == list(SECTORS9)
    assert QuantsmdCrossSectionalMomentumV2Bot.param_grid == {"skip": [0, 21]}
    assert QuantsmdCrossSectionalMomentumV2Bot.BACKTEST_PERIOD == "max"
