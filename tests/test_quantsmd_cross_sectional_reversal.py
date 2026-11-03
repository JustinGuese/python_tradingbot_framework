import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_cross_sectional_reversal import (
    GROSS,
    LOOKBACK,
    N_LONG,
    UNIVERSE,
    QuantsmdCrossSectionalReversalBot,
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


def test_three_weakest_names_held_at_one_sixth_each():
    # Rates increase with column position, so the first three columns have the
    # lowest trailing returns (the losers).
    rates = -0.002 + 0.0005 * np.arange(len(UNIVERSE))
    closes = _exp_frame(rates, 30)
    weights = compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG)
    assert weights is not None
    assert set(weights) == set(UNIVERSE[:3])
    for w in weights.values():
        assert w == pytest.approx(GROSS / N_LONG)


def test_gross_exposure_is_half():
    rates = -0.002 + 0.0005 * np.arange(len(UNIVERSE))
    closes = _exp_frame(rates, 30)
    weights = compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG)
    assert weights is not None
    assert sum(weights.values()) == pytest.approx(0.5)


def test_non_friday_last_bar_returns_none():
    closes = _random_walk(THURSDAY_END, 60)
    assert compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG) is None


def test_fewer_rows_than_lookback_plus_one_returns_empty():
    rates = -0.002 + 0.0005 * np.arange(len(UNIVERSE))
    # 21 rows is one short of the 22 needed for a 21-bar lookback.
    closes = _exp_frame(rates, LOOKBACK)
    assert compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG) == {}


def test_fewer_than_six_valid_names_returns_empty():
    rates = -0.002 + 0.0005 * np.arange(len(UNIVERSE))
    closes = _exp_frame(rates, 30)
    # Four names have no data on the last bar, leaving five valid names.
    for t in UNIVERSE[:4]:
        closes[t] = np.nan
    assert compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG) == {}


def test_exactly_six_valid_names_still_holds_three_losers():
    rates = -0.002 + 0.0005 * np.arange(len(UNIVERSE))
    closes = _exp_frame(rates, 30)
    for t in UNIVERSE[:3]:
        closes[t] = np.nan
    # Of the six valid names (columns 3-8) the three weakest are columns 3-5.
    weights = compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG)
    assert weights is not None
    assert set(weights) == set(UNIVERSE[3:6])


# --- Generic cases (G1-G3) ---


def test_generic_weights_are_nonnegative_and_sum_at_most_one():
    closes = _random_walk(FRIDAY_END, 600, seed=7)
    weights = compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG)
    assert weights is not None
    assert all(w >= 0.0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights
    assert set(weights) <= set(UNIVERSE)


def test_generic_too_short_history_returns_empty_or_none():
    closes = _random_walk(FRIDAY_END, 10)
    result = compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG)
    assert result is None or result == {}


def test_generic_bot_class_metadata_is_readable():
    assert list(UNIVERSE) == list(SECTORS9)
    assert "lookback" in QuantsmdCrossSectionalReversalBot.param_grid
    assert QuantsmdCrossSectionalReversalBot.BACKTEST_PERIOD == "max"
