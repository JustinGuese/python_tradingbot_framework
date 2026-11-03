import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_monthly_circuit_breaker import (
    BREAKER,
    CAP,
    COOLDOWN,
    FAST,
    SLOW,
    UNIVERSE,
    QuantsmdMonthlyCircuitBreakerBot,
    compute_weights,
)
from tradingbot.quantsmd_monthly_drift_etf import compute_weights as drift_weights
from tradingbot.utils.quantsmd_common import period_rolled


def _frame(columns: dict[str, np.ndarray]) -> pd.DataFrame:
    length = len(next(iter(columns.values())))
    index = pd.bdate_range("2019-01-01", periods=length)
    return pd.DataFrame(columns, index=index)


def _up(n: int, drift: float = 0.001) -> np.ndarray:
    return 100.0 * np.exp(drift * np.arange(n))


def _down(n: int, drift: float = 0.001) -> np.ndarray:
    return 100.0 * np.exp(-drift * np.arange(n))


def _month_start_positions(index: pd.DatetimeIndex) -> list[int]:
    return [k for k in range(1, len(index)) if period_rolled(index[: k + 1], "M")]


def _crash_frame(n: int = 600) -> tuple[pd.DataFrame, int]:
    """Rising ETF8 with a one-day 20% drop five bars after a month start.

    Returns the frame and the crash row position.
    """
    closes = _frame({t: _up(n) for t in UNIVERSE})
    k = next(p for p in _month_start_positions(closes.index) if p >= 260)
    crash = k + 5
    crashed = closes.copy()
    crashed.iloc[crash:] *= 0.8
    return crashed, crash


def test_fewer_than_200_rows_returns_empty():
    closes = _frame({t: _up(199) for t in UNIVERSE})
    assert compute_weights(closes, FAST, SLOW, CAP) == {}


def test_no_crash_equals_plain_monthly_book():
    closes = _frame({t: _up(600) for t in UNIVERSE})
    k = _month_start_positions(closes.index)[-1]
    closes = closes.iloc[: k + 1]
    weights = compute_weights(closes, FAST, SLOW, CAP)
    assert weights == drift_weights(closes, FAST, SLOW, CAP)
    assert set(weights) == set(UNIVERSE)
    for w in weights.values():
        assert w == pytest.approx(1 / 8)


def test_all_downtrending_returns_empty():
    closes = _frame({t: _down(600) for t in UNIVERSE})
    assert compute_weights(closes, FAST, SLOW, CAP) == {}


def test_crash_clears_book_for_next_ten_rows():
    closes, crash = _crash_frame()
    # Control: without the drop the book is still held on the crash bar.
    control = _frame({t: _up(600) for t in UNIVERSE}).iloc[: crash + 1]
    assert compute_weights(control, FAST, SLOW, CAP) != {}
    for j in range(crash, crash + 10):
        assert compute_weights(closes.iloc[: j + 1], FAST, SLOW, CAP) == {}


def test_generic_weights_nonnegative_and_bounded():
    rng = np.random.default_rng(7)
    n = 600
    cols = {}
    for t in UNIVERSE:
        steps = rng.normal(0.0002, 0.01, n)
        cols[t] = 100.0 * np.exp(np.cumsum(steps))
    closes = _frame(cols)
    weights = compute_weights(closes, FAST, SLOW, CAP)
    assert all(w >= 0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights
    assert set(weights) <= set(UNIVERSE)


def test_generic_too_short_history_never_raises():
    closes = _frame({t: _up(10) for t in UNIVERSE})
    assert compute_weights(closes, FAST, SLOW, CAP) == {}


def test_generic_bot_class_metadata_readable():
    assert QuantsmdMonthlyCircuitBreakerBot.param_grid == {
        "breaker": [0.10, 0.15, 0.20],
        "cooldown": [5, 10, 20],
    }
    assert QuantsmdMonthlyCircuitBreakerBot.BACKTEST_PERIOD == "max"
    assert BREAKER == 0.15
    assert COOLDOWN == 10
