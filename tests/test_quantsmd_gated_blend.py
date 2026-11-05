from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from tradingbot import quantsmd_gated_blend as blend
from tradingbot.quantsmd_gated_blend import (
    COMPONENT_WEIGHTS,
    UNIVERSE,
    QuantsmdGatedBlendBot,
    compute_weights,
)
from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF8, SECTORS9


def _frame(tickers: list[str], n: int = 50) -> pd.DataFrame:
    index = pd.bdate_range("2020-01-01", periods=n)
    data = {t: np.linspace(100.0, 110.0, n) for t in tickers}
    return pd.DataFrame(data, index=index)


def test_weights_are_applied(monkeypatch):
    monkeypatch.setattr(blend.sma_gated, "compute_weights", lambda *a, **kw: {"XLK": 0.5})
    monkeypatch.setattr(blend.reversal_v2, "compute_weights", lambda *a, **kw: {"XLE": 0.5})
    monkeypatch.setattr(blend.momentum_v2, "compute_weights", lambda *a, **kw: {"XLF": 0.5})
    monkeypatch.setattr(blend.te_gated, "compute_weights", lambda *a, **kw: {"SPY": 0.5})

    closes = _frame(UNIVERSE)
    weights = compute_weights(closes)

    # 0.5 * 0.4 = 0.2 for sma_gated; 0.5 * 0.2 = 0.1 for the other three.
    assert weights["XLK"] == pytest.approx(0.2)
    assert weights["XLE"] == pytest.approx(0.1)
    assert weights["XLF"] == pytest.approx(0.1)
    assert weights["SPY"] == pytest.approx(0.1)
    assert sum(weights.values()) == pytest.approx(0.5)


def test_none_component_contributes_nothing(monkeypatch):
    monkeypatch.setattr(blend.sma_gated, "compute_weights", lambda *a, **kw: {"XLK": 0.5})
    monkeypatch.setattr(blend.reversal_v2, "compute_weights", lambda *a, **kw: None)
    monkeypatch.setattr(blend.momentum_v2, "compute_weights", lambda *a, **kw: {})
    monkeypatch.setattr(blend.te_gated, "compute_weights", lambda *a, **kw: {})

    closes = _frame(UNIVERSE)
    weights = compute_weights(closes)

    assert weights == {"XLK": pytest.approx(0.2)}


def test_raising_component_is_skipped(monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("failed data fetch or ranking")

    monkeypatch.setattr(blend.sma_gated, "compute_weights", _boom)
    monkeypatch.setattr(blend.reversal_v2, "compute_weights", lambda *a, **kw: {"XLE": 0.5})
    monkeypatch.setattr(blend.momentum_v2, "compute_weights", lambda *a, **kw: {})
    monkeypatch.setattr(blend.te_gated, "compute_weights", lambda *a, **kw: {})

    closes = _frame(UNIVERSE)
    weights = compute_weights(closes)

    assert "XLK" not in weights
    assert weights == {"XLE": pytest.approx(0.1)}


def test_gross_scaled_to_max_gross(monkeypatch):
    monkeypatch.setattr(blend.sma_gated, "compute_weights", lambda *a, **kw: {"XLK": 1.0, "SPY": 1.0})
    monkeypatch.setattr(blend.reversal_v2, "compute_weights", lambda *a, **kw: {"XLK": 1.0, "SPY": 1.0})
    monkeypatch.setattr(blend.momentum_v2, "compute_weights", lambda *a, **kw: {"XLK": 1.0, "SPY": 1.0})
    monkeypatch.setattr(blend.te_gated, "compute_weights", lambda *a, **kw: {"XLK": 1.0, "SPY": 1.0})

    closes = _frame(UNIVERSE)
    weights = compute_weights(closes)

    # Gross would be 2.0 unscaled, scaled down to 1.0 (0.5 each).
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert sum(weights.values()) == pytest.approx(1.0)
    assert weights["XLK"] == pytest.approx(0.5)
    assert weights["SPY"] == pytest.approx(0.5)


def test_same_ticker_in_multiple_components_adds(monkeypatch):
    monkeypatch.setattr(blend.sma_gated, "compute_weights", lambda *a, **kw: {"SPY": 0.25})
    monkeypatch.setattr(blend.reversal_v2, "compute_weights", lambda *a, **kw: {})
    monkeypatch.setattr(blend.momentum_v2, "compute_weights", lambda *a, **kw: {})
    monkeypatch.setattr(blend.te_gated, "compute_weights", lambda *a, **kw: {"SPY": 0.25})

    closes = _frame(UNIVERSE)
    weights = compute_weights(closes)

    # SPY: 0.25 * 0.4 + 0.25 * 0.2 = 0.10 + 0.05 = 0.15.
    assert weights == {"SPY": pytest.approx(0.15)}


def test_bot_constructs(monkeypatch):
    assert COMPONENT_WEIGHTS == {
        "sma_gated": 0.4,
        "reversal": 0.2,
        "momentum": 0.2,
        "te_gated": 0.2,
    }
    assert QuantsmdGatedBlendBot.param_grid == {}
    assert QuantsmdGatedBlendBot.BACKTEST_PERIOD == "max"

    captured: dict = {}

    def fake_init(self, name, **kwargs):
        captured["name"] = name
        captured.update(kwargs)

    monkeypatch.setattr(Bot, "__init__", fake_init)
    bot = QuantsmdGatedBlendBot()
    assert captured["name"] == "QuantsmdGatedBlendBot"
    assert captured["tickers"] == UNIVERSE
    assert captured["benchmark_tickers"] == []
    assert captured["interval"] == "1d"
    assert captured["period"] == "2y"
    assert bot.weights is None


def test_custom_weights(monkeypatch):
    monkeypatch.setattr(blend.sma_gated, "compute_weights", lambda *a, **kw: {"XLK": 0.5})
    monkeypatch.setattr(blend.reversal_v2, "compute_weights", lambda *a, **kw: {"XLE": 0.5})
    monkeypatch.setattr(blend.momentum_v2, "compute_weights", lambda *a, **kw: {})
    monkeypatch.setattr(blend.te_gated, "compute_weights", lambda *a, **kw: {})

    closes = _frame(UNIVERSE)
    custom = {"sma_gated": 0.7, "reversal": 0.3}
    weights = compute_weights(closes, weights=custom)

    assert weights["XLK"] == pytest.approx(0.5 * 0.7)
    assert weights["XLE"] == pytest.approx(0.5 * 0.3)


def test_empty_closes_returns_empty_book():
    empty_df = pd.DataFrame()
    assert compute_weights(empty_df) == {}


def test_relevant_columns_passed_to_components(monkeypatch):
    captured: dict[str, list[str]] = {}

    def _cap(name):
        def _fn(sub_closes, *a, **kw):
            captured[name] = list(sub_closes.columns)
            return {}

        return _fn

    monkeypatch.setattr(blend.sma_gated, "compute_weights", _cap("sma_gated"))
    monkeypatch.setattr(blend.reversal_v2, "compute_weights", _cap("reversal"))
    monkeypatch.setattr(blend.momentum_v2, "compute_weights", _cap("momentum"))
    monkeypatch.setattr(blend.te_gated, "compute_weights", _cap("te_gated"))

    closes = _frame(UNIVERSE)
    compute_weights(closes)

    # sma_gated should receive sectors + SPY
    assert set(captured["sma_gated"]) == set(SECTORS9) | {"SPY"}
    # reversal and momentum should receive only sectors (no SPY, no other ETF8)
    assert set(captured["reversal"]) == set(SECTORS9)
    assert set(captured["momentum"]) == set(SECTORS9)
    # te_gated should receive ETF8
    assert set(captured["te_gated"]) == set(ETF8)


def test_target_weights_delegates_to_compute_weights(monkeypatch):
    monkeypatch.setattr(Bot, "__init__", lambda self, *a, **kw: None)
    bot = QuantsmdGatedBlendBot()
    bot.tickers = list(UNIVERSE)
    bot.benchmark_tickers = []
    bot.weights = None
    sample_df = _frame(UNIVERSE)
    bot.datas = {t: pd.DataFrame({"close": sample_df[t]}) for t in UNIVERSE}
    monkeypatch.setattr(blend, "compute_weights", MagicMock(return_value={"XLK": 0.2}))

    res = bot.targetWeights({})
    assert res == {"XLK": 0.2}
    blend.compute_weights.assert_called_once()
    args, _ = blend.compute_weights.call_args
    passed_closes = args[0]
    assert set(passed_closes.columns) == set(UNIVERSE)
