from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import tradingbot.quantsmd_order_ensemble as ens
from tradingbot.quantsmd_order_ensemble import (
    COMPONENTS,
    UNIVERSE,
    QuantsmdOrderEnsembleBot,
    compute_weights,
)


def _ohlc(closes: dict[str, np.ndarray]) -> dict[str, pd.DataFrame]:
    out = {}
    for ticker, close in closes.items():
        close = np.asarray(close, dtype=float)
        index = pd.bdate_range("2020-01-01", periods=len(close))
        out[ticker] = pd.DataFrame(
            {"open": close, "high": close, "low": close, "close": close},
            index=index,
        )
    return out


def _flat(n: int = 120, price: float = 100.0) -> np.ndarray:
    return np.full(n, price)


def _noisy(n: int = 120) -> np.ndarray:
    # Alternating +/-2% daily moves: annualised vol around 0.32.
    steps = np.where(np.arange(n) % 2 == 0, 1.02, 0.98)
    return 100.0 * np.cumprod(steps)


def _fake(book: dict[str, float]):
    return SimpleNamespace(__name__="fake", compute_weights=lambda ohlc: dict(book))


def _failing():
    def boom(ohlc):
        raise RuntimeError("bad component")

    return SimpleNamespace(__name__="failing", compute_weights=boom)


def test_failing_component_is_skipped(monkeypatch):
    good = _fake({"SPY": 0.15})
    monkeypatch.setattr(ens, "COMPONENTS", (_failing(), good, good))
    ohlc = _ohlc({"SPY": _flat(), "SHV": _flat()})
    weights = compute_weights(ohlc, sleeve_boost=4.0)
    # Two good components of three give SPY (0.15 + 0.15) / 3 = 0.1; boosted x4 = 0.4.
    assert weights["SPY"] == pytest.approx(0.4)
    assert weights["SHV"] == pytest.approx(0.6)


def test_averaging_maths_with_two_fake_components(monkeypatch):
    a = _fake({"SPY": 0.2})
    b = _fake({"SPY": 0.1, "QQQ": 0.1})
    monkeypatch.setattr(ens, "COMPONENTS", (a, b))
    ohlc = _ohlc({"SPY": _flat(), "QQQ": _flat(), "SHV": _flat()})
    weights = compute_weights(ohlc, sleeve_boost=4.0)
    # SPY (0.2 + 0.1) / 2 * 4 = 0.6; QQQ 0.1 / 2 * 4 = 0.2; residual 0.2 to SHV.
    assert weights["SPY"] == pytest.approx(0.6)
    assert weights["QQQ"] == pytest.approx(0.2)
    assert weights["SHV"] == pytest.approx(0.2)


def test_result_has_shv_key_when_under_invested(monkeypatch):
    monkeypatch.setattr(ens, "COMPONENTS", (_fake({"SPY": 0.1}),))
    ohlc = _ohlc({"SPY": _flat(), "SHV": _flat()})
    weights = compute_weights(ohlc, sleeve_boost=4.0)
    assert weights["SPY"] == pytest.approx(0.4)
    assert "SHV" in weights
    assert weights["SHV"] == pytest.approx(0.6)


def test_gross_is_capped_at_one(monkeypatch):
    monkeypatch.setattr(ens, "COMPONENTS", (_fake({"SPY": 0.5, "QQQ": 0.5}),))
    ohlc = _ohlc({"SPY": _flat(), "QQQ": _flat(), "SHV": _flat()})
    weights = compute_weights(ohlc, sleeve_boost=4.0)
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert sum(weights.values()) == pytest.approx(1.0)
    assert "SHV" not in weights


def test_empty_books_go_to_shv(monkeypatch):
    monkeypatch.setattr(ens, "COMPONENTS", (_fake({}), _fake({})))
    ohlc = _ohlc({"SPY": _flat(), "SHV": _flat()})
    assert compute_weights(ohlc) == {"SHV": 1.0}


def test_vol_target_scales_risky_book_down(monkeypatch):
    monkeypatch.setattr(ens, "COMPONENTS", (_fake({"SPY": 0.1}),))
    ohlc = _ohlc({"SPY": _noisy(), "SHV": _flat()})
    low = compute_weights(ohlc, target_vol=0.06, sleeve_boost=4.0)
    high = compute_weights(ohlc, target_vol=0.14, sleeve_boost=4.0)
    # Book vol is 0.4 * ~0.32 = ~0.13, so the 0.06 target scales the book down and the
    # 0.14 target scales it up. A higher target must give more SPY.
    assert low["SPY"] < 0.4 < high["SPY"]
    assert sum(low.values()) <= 1.0 + 1e-9


def test_real_components_run_on_uptrend():
    n = 300
    drift = 100.0 * np.exp(0.001 * np.arange(n))
    ohlc = _ohlc({"SPY": drift, "QQQ": drift, "IWM": drift, "SHV": _flat(n)})
    weights = compute_weights(ohlc)
    assert set(weights) <= set(UNIVERSE)
    assert sum(weights.values()) <= 1.0 + 1e-9


def test_bot_class_metadata_readable():
    assert UNIVERSE == ["SPY", "QQQ", "IWM", "SHV"]
    assert len(COMPONENTS) == 12
    assert QuantsmdOrderEnsembleBot.param_grid == {
        "target_vol": [0.06, 0.10, 0.14],
        "sleeve_boost": [2.0, 4.0],
    }
    assert QuantsmdOrderEnsembleBot.BACKTEST_PERIOD == "max"
