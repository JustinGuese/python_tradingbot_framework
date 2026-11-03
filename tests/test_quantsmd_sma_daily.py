import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_sma_daily import (
    CAP,
    FAST,
    SLOW,
    UNIVERSE,
    QuantsmdSmaDailyBot,
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


def test_all_uptrending_equal_weight_below_cap():
    n = 300
    closes = _frame({t: _up(n) for t in UNIVERSE})
    weights = compute_weights(closes, FAST, SLOW, CAP)
    assert set(weights) == set(UNIVERSE)
    for w in weights.values():
        assert w == pytest.approx(1 / 9)
        assert w < CAP


def test_only_two_active_cap_binds():
    n = 300
    cols = {t: _down(n) for t in UNIVERSE}
    cols[UNIVERSE[0]] = _up(n)
    cols[UNIVERSE[1]] = _up(n)
    closes = _frame(cols)
    weights = compute_weights(closes, FAST, SLOW, CAP)
    assert set(weights) == {UNIVERSE[0], UNIVERSE[1]}
    for w in weights.values():
        assert w == pytest.approx(0.25)
    assert sum(weights.values()) == pytest.approx(0.5)


def test_all_downtrending_returns_empty():
    n = 300
    closes = _frame({t: _down(n) for t in UNIVERSE})
    assert compute_weights(closes, FAST, SLOW, CAP) == {}


def test_199_rows_returns_empty():
    closes = _frame({t: _up(199) for t in UNIVERSE})
    assert compute_weights(closes, FAST, SLOW, CAP) == {}


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


def test_generic_too_short_history_never_raises():
    closes = _frame({t: _up(10) for t in UNIVERSE})
    assert compute_weights(closes, FAST, SLOW, CAP) == {}


def test_generic_bot_class_metadata_readable():
    assert QuantsmdSmaDailyBot.param_grid == {
        "fast": [20, 50, 100],
        "slow": [150, 200, 250],
    }
    assert hasattr(QuantsmdSmaDailyBot, "BACKTEST_PERIOD")
