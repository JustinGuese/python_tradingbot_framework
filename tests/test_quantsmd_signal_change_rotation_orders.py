import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_signal_change_rotation_orders import (
    FAST,
    SIZE,
    SLOW,
    UNIVERSE,
    QuantsmdSignalChangeRotationOrdersBot,
    compute_weights,
)


def _frame(columns: dict[str, np.ndarray]) -> pd.DataFrame:
    n = len(next(iter(columns.values())))
    index = pd.bdate_range("2020-01-01", periods=n)
    return pd.DataFrame(columns, index=index)


def _up(n: int, drift: float = 0.001) -> np.ndarray:
    return 100.0 * np.exp(drift * np.arange(n))


def _down(n: int, drift: float = 0.001) -> np.ndarray:
    return 100.0 * np.exp(-drift * np.arange(n))


def _flat_then_jump(n: int, jump_at_last: float = 200.0) -> np.ndarray:
    # Flat at 100 so both SMAs are equal on bar n-2 (no signal), then the last
    # bar jumps, pushing the 20-bar SMA above the 50-bar SMA on bar n-1 only.
    values = np.full(n, 100.0)
    values[-1] = jump_at_last
    return values


def test_all_uptrending_no_flip_returns_none():
    # Steady uptrend: the signal is 1 on every bar, so nothing flipped on the last bar.
    n = 300
    closes = _frame({t: _up(n) for t in UNIVERSE})
    assert compute_weights(closes, FAST, SLOW, SIZE) is None


def test_flat_history_no_flip_returns_none():
    closes = _frame({t: np.full(60, 100.0) for t in UNIVERSE})
    assert compute_weights(closes, FAST, SLOW, SIZE) is None


def test_flip_on_last_bar_returns_full_book():
    n = 51
    closes = _frame({t: _flat_then_jump(n) for t in UNIVERSE})
    weights = compute_weights(closes, FAST, SLOW, SIZE)
    assert weights is not None
    assert set(weights) == set(UNIVERSE)
    for w in weights.values():
        assert w == pytest.approx(0.18)


def test_flip_down_on_last_bar_returns_empty_book():
    # A name that flips from up to down on the last bar is exited; the book is
    # {} on a flip bar, not None, because a flip happened.
    n = 300
    values = _up(n)
    values[-1] = values[-1] * 0.5
    closes = _frame(dict.fromkeys(UNIVERSE, values))
    weights = compute_weights(closes, FAST, SLOW, SIZE)
    assert weights == {}


def test_50_rows_returns_empty():
    closes = _frame({t: _flat_then_jump(50) for t in UNIVERSE})
    assert compute_weights(closes, FAST, SLOW, SIZE) == {}


def test_generic_weights_nonnegative_and_bounded():
    rng = np.random.default_rng(7)
    n = 600
    cols = {}
    for t in UNIVERSE:
        steps = rng.normal(0.0002, 0.01, n)
        cols[t] = 100.0 * np.exp(np.cumsum(steps))
    closes = _frame(cols)
    weights = compute_weights(closes, FAST, SLOW, SIZE)
    if weights is not None:
        assert all(w >= 0 for w in weights.values())
        assert sum(weights.values()) <= 1.0 + 1e-9
        assert "USD" not in weights
        assert set(weights) <= set(UNIVERSE)


def test_generic_too_short_history_never_raises():
    closes = _frame({t: _up(10) for t in UNIVERSE})
    assert compute_weights(closes, FAST, SLOW, SIZE) == {}


def test_generic_bot_class_metadata_readable():
    assert QuantsmdSignalChangeRotationOrdersBot.param_grid == {
        "fast": [10, 20, 50],
        "slow": [50, 100, 200],
    }
    assert hasattr(QuantsmdSignalChangeRotationOrdersBot, "BACKTEST_PERIOD")
    assert QuantsmdSignalChangeRotationOrdersBot.BACKTEST_PERIOD == "max"
