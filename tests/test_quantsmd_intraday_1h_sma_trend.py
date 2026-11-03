import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_intraday_1h_sma_trend import (
    CAP,
    FAST,
    SLOW,
    UNIVERSE,
    QuantsmdIntraday1hSmaTrendBot,
    compute_weights,
)
from tradingbot.utils.botclass import Bot


def _frame(columns: dict[str, np.ndarray]) -> pd.DataFrame:
    n = len(next(iter(columns.values())))
    index = pd.date_range("2026-01-05 14:00", periods=n, freq="1h")
    return pd.DataFrame(columns, index=index)


def _up(n: int, drift: float = 0.001) -> np.ndarray:
    return 100.0 * np.exp(drift * np.arange(n))


def _down(n: int, drift: float = 0.001) -> np.ndarray:
    return 100.0 * np.exp(-drift * np.arange(n))


def _reversal(n_fall: int = 30, n_rise: int = 30) -> np.ndarray:
    fall = np.linspace(100.0, 90.0, n_fall)
    rise = np.linspace(90.0, 130.0, n_rise + 1)[1:]
    return np.concatenate([fall, rise])


def test_all_uptrending_equal_weight_is_capped_at_quarter():
    n = 60
    closes = _frame({t: _up(n) for t in UNIVERSE})
    weights = compute_weights(closes, FAST, SLOW, CAP)
    assert set(weights) == set(UNIVERSE)
    for w in weights.values():
        assert w == pytest.approx(0.25)
    assert sum(weights.values()) == pytest.approx(0.75)


def test_only_one_active_gets_quarter():
    n = 60
    cols = {t: _down(n) for t in UNIVERSE}
    cols[UNIVERSE[0]] = _reversal()
    closes = _frame(cols)
    weights = compute_weights(closes, FAST, SLOW, CAP)
    assert weights == {UNIVERSE[0]: pytest.approx(0.25)}


def test_all_downtrending_returns_empty():
    n = 60
    closes = _frame({t: _down(n) for t in UNIVERSE})
    assert compute_weights(closes, FAST, SLOW, CAP) == {}


def test_29_rows_returns_empty():
    closes = _frame({t: _up(29) for t in UNIVERSE})
    assert compute_weights(closes, FAST, SLOW, CAP) == {}


def test_30_rows_uptrend_is_evaluable():
    closes = _frame({t: _up(30) for t in UNIVERSE})
    weights = compute_weights(closes, FAST, SLOW, CAP)
    assert set(weights) == set(UNIVERSE)


def test_generic_weights_nonnegative_and_bounded():
    rng = np.random.default_rng(7)
    n = 600
    cols = {}
    for t in UNIVERSE:
        steps = rng.normal(0.0, 0.005, n)
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
    assert QuantsmdIntraday1hSmaTrendBot.param_grid == {
        "fast": [5, 10, 20],
        "slow": [30, 50, 60],
    }
    assert QuantsmdIntraday1hSmaTrendBot.BACKTEST_PERIOD == "1y"


def test_bot_name_interval_period_and_universe(monkeypatch):
    captured: dict = {}

    def fake_init(self, name, **kwargs):
        captured["name"] = name
        captured.update(kwargs)

    monkeypatch.setattr(Bot, "__init__", fake_init)
    QuantsmdIntraday1hSmaTrendBot()
    assert captured["name"] == "QuantsmdIntraday1hSmaTrendBot"
    assert captured["tickers"] == list(UNIVERSE) == ["SPY", "QQQ", "IWM"]
    assert captured["interval"] == "1h"
    assert captured["period"] == "3mo"
