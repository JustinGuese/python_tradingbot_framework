import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_cross_sectional_momentum import (
    GROSS,
    LOOKBACK,
    N_LONG,
    UNIVERSE,
    QuantsmdCrossSectionalMomentumBot,
    compute_weights,
)
from tradingbot.utils.quantsmd_common import SECTORS9

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
    returns = rng.normal(0.0004, 0.01, size=(periods, len(UNIVERSE)))
    closes = 100.0 * np.exp(np.cumsum(returns, axis=0))
    return pd.DataFrame(closes, index=idx, columns=UNIVERSE)


def _exp_frame(rates: np.ndarray, periods: int, end: str = MONTH_FIRST_END) -> pd.DataFrame:
    """Deterministic exponential growth per column, so the ranking is known."""
    idx = _index(end, periods)
    closes = 100.0 * np.exp(np.outer(np.arange(periods), rates))
    return pd.DataFrame(closes, index=idx, columns=UNIVERSE)


def test_top_three_momentum_names_held_at_one_sixth_each():
    # Rates increase with column position, so the last three columns have the
    # highest trailing momentum.
    rates = 0.0002 + 0.0004 * np.arange(len(UNIVERSE))
    closes = _exp_frame(rates, 300)
    weights = compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG)
    assert weights is not None
    assert set(weights) == set(UNIVERSE[-3:])
    for w in weights.values():
        assert w == pytest.approx(GROSS / N_LONG)


def test_gross_exposure_is_half():
    rates = 0.0002 + 0.0004 * np.arange(len(UNIVERSE))
    closes = _exp_frame(rates, 300)
    weights = compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG)
    assert weights is not None
    assert sum(weights.values()) == pytest.approx(0.5)


def test_mid_month_bar_returns_none():
    closes = _random_walk(MID_MONTH_END, 300)
    assert compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG) is None


def test_fewer_rows_than_lookback_plus_one_returns_empty():
    rates = 0.0002 + 0.0004 * np.arange(len(UNIVERSE))
    # 252 rows is one short of the 253 needed for a 252-bar lookback.
    closes = _exp_frame(rates, LOOKBACK)
    assert compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG) == {}


def test_fewer_than_six_valid_names_returns_empty():
    rates = 0.0002 + 0.0004 * np.arange(len(UNIVERSE))
    closes = _exp_frame(rates, 300)
    # Four names have no data on the last bar, leaving five valid names.
    for t in UNIVERSE[:4]:
        closes[t] = np.nan
    assert compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG) == {}


def test_exactly_six_valid_names_still_holds_three():
    rates = 0.0002 + 0.0004 * np.arange(len(UNIVERSE))
    closes = _exp_frame(rates, 300)
    for t in UNIVERSE[:3]:
        closes[t] = np.nan
    weights = compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG)
    assert weights is not None
    assert set(weights) == set(UNIVERSE[-3:])


# --- Generic cases (G1-G3) ---


def test_generic_weights_are_nonnegative_and_sum_at_most_one():
    closes = _random_walk(MONTH_FIRST_END, 600, seed=7)
    weights = compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG)
    assert weights is not None
    assert all(w >= 0.0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights
    assert set(weights) <= set(UNIVERSE)


def test_generic_too_short_history_returns_empty_or_none():
    closes = _random_walk(MONTH_FIRST_END, 10)
    result = compute_weights(closes, lookback=LOOKBACK, n_long=N_LONG)
    assert result is None or result == {}


def test_generic_bot_class_metadata_is_readable():
    assert list(UNIVERSE) == list(SECTORS9)
    assert "lookback" in QuantsmdCrossSectionalMomentumBot.param_grid
    assert QuantsmdCrossSectionalMomentumBot.BACKTEST_PERIOD == "max"
