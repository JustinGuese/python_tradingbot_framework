import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_macd_trend import (
    CAP,
    FAST,
    SIGNAL,
    SLOW,
    UNIVERSE,
    QuantsmdMacdTrendBot,
    compute_weights,
)


def _frame(columns: dict[str, np.ndarray]) -> pd.DataFrame:
    n = len(next(iter(columns.values())))
    index = pd.bdate_range("2020-01-01", periods=n)
    return pd.DataFrame(columns, index=index)


def _up(n: int, drift: float = 0.001) -> np.ndarray:
    return 100.0 * np.exp(drift * np.arange(n))


def _crash(n: int, tail: int = 10) -> np.ndarray:
    # A steady rise, then a sharp slide over the last ``tail`` bars. A constant
    # downward drift would not do: its MACD line keeps rising toward zero, so it
    # stays above the signal line and reads as active.
    base = 100.0 * np.exp(0.001 * np.arange(n - tail))
    slide = base[-1] * np.exp(-0.03 * np.arange(1, tail + 1))
    return np.concatenate([base, slide])


def test_all_uptrending_equal_weight_below_cap():
    n = 300
    closes = _frame({t: _up(n) for t in UNIVERSE})
    weights = compute_weights(closes, FAST, SLOW, SIGNAL)
    assert set(weights) == set(UNIVERSE)
    for w in weights.values():
        assert w == pytest.approx(1 / 9)
        assert w < CAP


def test_only_two_active_cap_binds():
    n = 300
    cols = {t: _crash(n) for t in UNIVERSE}
    cols[UNIVERSE[0]] = _up(n)
    cols[UNIVERSE[1]] = _up(n)
    closes = _frame(cols)
    weights = compute_weights(closes, FAST, SLOW, SIGNAL)
    assert set(weights) == {UNIVERSE[0], UNIVERSE[1]}
    for w in weights.values():
        assert w == pytest.approx(0.25)
    assert sum(weights.values()) == pytest.approx(0.5)


def test_all_sliding_returns_empty():
    n = 300
    closes = _frame({t: _crash(n) for t in UNIVERSE})
    assert compute_weights(closes, FAST, SLOW, SIGNAL) == {}


def test_34_rows_returns_empty():
    closes = _frame({t: _up(34) for t in UNIVERSE})
    assert compute_weights(closes, FAST, SLOW, SIGNAL) == {}


def test_flat_series_is_not_active():
    # MACD and its signal are both zero on a constant price: not strictly above.
    closes = _frame({t: np.full(100, 50.0) for t in UNIVERSE})
    assert compute_weights(closes, FAST, SLOW, SIGNAL) == {}


def test_generic_weights_nonnegative_and_bounded():
    rng = np.random.default_rng(7)
    n = 600
    cols = {}
    for t in UNIVERSE:
        steps = rng.normal(0.0002, 0.01, n)
        cols[t] = 100.0 * np.exp(np.cumsum(steps))
    closes = _frame(cols)
    weights = compute_weights(closes, FAST, SLOW, SIGNAL)
    assert all(w >= 0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights
    assert set(weights) <= set(UNIVERSE)


def test_generic_too_short_history_never_raises():
    closes = _frame({t: _up(10) for t in UNIVERSE})
    assert compute_weights(closes, FAST, SLOW, SIGNAL) == {}


def test_generic_bot_class_metadata_readable():
    assert QuantsmdMacdTrendBot.param_grid == {
        "fast": [8, 12],
        "slow": [21, 26],
        "signal": [7, 9],
    }
