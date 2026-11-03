import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_intraday_1m_ema_scalp import (
    CAP,
    FAST,
    SLOW,
    UNIVERSE,
    QuantsmdIntraday1mEmaScalpBot,
    compute_weights,
)
from tradingbot.utils.botclass import Bot


def _frame(columns: dict[str, np.ndarray]) -> pd.DataFrame:
    n = len(next(iter(columns.values())))
    index = pd.date_range("2026-01-05 14:30", periods=n, freq="1min")
    return pd.DataFrame(columns, index=index)


def _up(n: int, drift: float = 0.001) -> np.ndarray:
    return 100.0 * np.exp(drift * np.arange(n))


def _down(n: int, drift: float = 0.001) -> np.ndarray:
    return 100.0 * np.exp(-drift * np.arange(n))


def test_all_uptrending_equal_weight_under_cap():
    n = 60
    closes = _frame({t: _up(n) for t in UNIVERSE})
    weights = compute_weights(closes, FAST, SLOW, CAP)
    assert set(weights) == set(UNIVERSE)
    for w in weights.values():
        assert w == pytest.approx(1 / 3)
        assert w < CAP


def test_only_one_active_cap_binds():
    n = 60
    cols = {t: _down(n) for t in UNIVERSE}
    cols[UNIVERSE[0]] = _up(n)
    closes = _frame(cols)
    weights = compute_weights(closes, FAST, SLOW, CAP)
    assert weights == {UNIVERSE[0]: pytest.approx(CAP)}


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
        steps = rng.normal(0.0, 0.001, n)
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
    assert QuantsmdIntraday1mEmaScalpBot.param_grid == {
        "fast": [5, 9, 12],
        "slow": [21, 26, 34],
    }
    assert QuantsmdIntraday1mEmaScalpBot.BACKTEST_PERIOD == "7d"


def test_bot_name_interval_period_and_universe(monkeypatch):
    captured: dict = {}

    def fake_init(self, name, **kwargs):
        captured["name"] = name
        captured.update(kwargs)

    monkeypatch.setattr(Bot, "__init__", fake_init)
    QuantsmdIntraday1mEmaScalpBot()
    assert captured["name"] == "QuantsmdIntraday1mEmaScalpBot"
    assert captured["tickers"] == list(UNIVERSE) == ["SPY", "QQQ", "IWM"]
    assert captured["interval"] == "1m"
    assert captured["period"] == "5d"
