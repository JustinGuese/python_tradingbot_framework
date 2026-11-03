import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_signal_change_defensive import (
    CAP,
    DEFENSIVE,
    RISK_ON,
    UNIVERSE,
    WINDOW,
    QuantsmdSignalChangeDefensiveBot,
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


def test_spy_above_sma_gives_risk_on_book():
    n = 300
    closes = _frame({t: _up(n) for t in UNIVERSE})
    weights = compute_weights(closes, WINDOW, CAP)
    assert set(weights) == set(RISK_ON)
    for w in weights.values():
        assert w == pytest.approx(0.2)


def test_spy_below_sma_gives_defensive_book():
    n = 300
    cols = {t: _up(n) for t in UNIVERSE}
    cols["SPY"] = _down(n)
    closes = _frame(cols)
    weights = compute_weights(closes, WINDOW, CAP)
    assert set(weights) == set(DEFENSIVE)
    for w in weights.values():
        assert w == pytest.approx(1 / 3)
        assert w < CAP


def test_199_rows_returns_empty():
    closes = _frame({t: _up(199) for t in UNIVERSE})
    assert compute_weights(closes, WINDOW, CAP) == {}


def test_missing_spy_column_returns_empty():
    n = 300
    closes = _frame({t: _up(n) for t in UNIVERSE if t != "SPY"})
    assert compute_weights(closes, WINDOW, CAP) == {}


def test_generic_weights_nonnegative_and_bounded():
    rng = np.random.default_rng(7)
    n = 600
    cols = {}
    for t in UNIVERSE:
        steps = rng.normal(0.0002, 0.01, n)
        cols[t] = 100.0 * np.exp(np.cumsum(steps))
    closes = _frame(cols)
    weights = compute_weights(closes, WINDOW, CAP)
    assert all(w >= 0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights
    assert set(weights) <= set(UNIVERSE)


def test_generic_too_short_history_never_raises():
    closes = _frame({t: _up(10) for t in UNIVERSE})
    assert compute_weights(closes, WINDOW, CAP) == {}


def test_generic_bot_class_metadata_readable():
    assert QuantsmdSignalChangeDefensiveBot.param_grid == {
        "window": [150, 200, 250],
    }
    assert hasattr(QuantsmdSignalChangeDefensiveBot, "BACKTEST_PERIOD")
