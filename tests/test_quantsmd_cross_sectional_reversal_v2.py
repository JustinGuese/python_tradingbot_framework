import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_cross_sectional_reversal import (
    compute_weights as original_compute_weights,
)
from tradingbot.quantsmd_cross_sectional_reversal_v2 import (
    BUFFER,
    GROSS,
    LOOKBACK,
    N_LONG,
    UNIVERSE,
    QuantsmdCrossSectionalReversalV2Bot,
    compute_weights,
)
from tradingbot.utils.quantsmd_common import SECTORS9

FRIDAY_END = "2024-01-05"  # a known Friday
THURSDAY_END = "2024-01-04"  # the day before


def _index(end: str, periods: int) -> pd.DatetimeIndex:
    return pd.bdate_range(end=end, periods=periods)


def _random_walk(end: str, periods: int, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = _index(end, periods)
    returns = rng.normal(0.0, 0.01, size=(periods, len(UNIVERSE)))
    closes = 100.0 * np.exp(np.cumsum(returns, axis=0))
    return pd.DataFrame(closes, index=idx, columns=UNIVERSE)


def _exp_frame(rates: np.ndarray, periods: int, end: str = FRIDAY_END) -> pd.DataFrame:
    """Deterministic exponential growth per column, so the ranking is known."""
    idx = _index(end, periods)
    closes = 100.0 * np.exp(np.outer(np.arange(periods), rates))
    return pd.DataFrame(closes, index=idx, columns=UNIVERSE)


def _two_fridays(prev_week: np.ndarray, last_week: np.ndarray) -> pd.DataFrame:
    """Eleven bars with Fridays at rows 0, 5 and 10; only the two later Fridays move.

    With ``lookback=1`` the ranking on a Friday is that bar's own log return, so the
    per-name log returns on rows 5 and 10 set the weekly ranking exactly.
    """
    idx = _index(FRIDAY_END, 11)
    returns = np.zeros((11, len(UNIVERSE)))
    returns[5] = prev_week
    returns[10] = last_week
    closes = 100.0 * np.exp(np.cumsum(returns, axis=0))
    return pd.DataFrame(closes, index=idx, columns=UNIVERSE)


def test_buffer_keeps_holding_that_slipped_to_rank_n_long_plus_one():
    # XLB is the weakest name on the previous Friday, so it is held with XLE.
    prev = np.array([-0.03, -0.02, 0.011, 0.012, 0.013, 0.014, 0.015, 0.016, 0.017])
    # On the last Friday XLF and XLI are the two weakest and XLB has slipped to rank 3.
    last = np.array([-0.01, 0.0, -0.04, -0.03, 0.021, 0.022, 0.023, 0.024, 0.025])
    closes = _two_fridays(prev, last)
    kept = compute_weights(closes, lookback=1, n_long=2, buffer=1)
    assert kept is not None
    assert set(kept) == {UNIVERSE[0], UNIVERSE[2]}  # XLB kept (rank 3 <= 2 + 1)
    dropped = compute_weights(closes, lookback=1, n_long=2, buffer=0)
    assert dropped is not None
    assert set(dropped) == {UNIVERSE[2], UNIVERSE[3]}  # XLB sold: rank 3 > 2


def test_buffer_zero_matches_original_on_random_walks():
    for seed in range(5):
        closes = _random_walk(FRIDAY_END, 600, seed=seed)
        expected = original_compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG)
        got = compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG, buffer=0)
        assert got == expected


def test_three_weakest_names_held_at_one_sixth_each():
    rates = -0.002 + 0.0005 * np.arange(len(UNIVERSE))
    closes = _exp_frame(rates, 30)
    weights = compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG)
    assert set(weights) == set(UNIVERSE[:3])
    for w in weights.values():
        assert w == pytest.approx(GROSS / N_LONG)
    assert sum(weights.values()) == pytest.approx(0.5)


def test_non_friday_last_bar_returns_none():
    closes = _random_walk(THURSDAY_END, 60)
    assert compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG, buffer=BUFFER) is None


def test_fewer_rows_than_lookback_plus_one_returns_empty():
    rates = -0.002 + 0.0005 * np.arange(len(UNIVERSE))
    closes = _exp_frame(rates, LOOKBACK)
    assert compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG) == {}


def test_fewer_than_six_valid_names_returns_empty():
    rates = -0.002 + 0.0005 * np.arange(len(UNIVERSE))
    closes = _exp_frame(rates, 30)
    for t in UNIVERSE[:4]:
        closes[t] = np.nan
    assert compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG) == {}


def test_generic_weights_nonnegative_and_sum_at_most_one():
    closes = _random_walk(FRIDAY_END, 600, seed=7)
    weights = compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG, buffer=BUFFER)
    assert all(w >= 0.0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights
    assert set(weights) <= set(UNIVERSE)


def test_generic_bot_class_metadata_is_readable():
    assert list(UNIVERSE) == list(SECTORS9)
    assert QuantsmdCrossSectionalReversalV2Bot.param_grid == {"buffer": [0, 2, 3]}
    assert QuantsmdCrossSectionalReversalV2Bot.BACKTEST_PERIOD == "max"
