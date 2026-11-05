import numpy as np
import pandas as pd
import pytest

from tradingbot import quantsmd_sma_daily as base
from tradingbot.quantsmd_sma_daily_gated_v3 import (
    CAP,
    FAST,
    SLOW,
    UNIVERSE,
    QuantsmdSmaDailyGatedV3Bot,
    compute_weights,
)
from tradingbot.utils.botclass import Bot


def _frame(columns: dict[str, np.ndarray]) -> pd.DataFrame:
    n = len(next(iter(columns.values())))
    index = pd.bdate_range("2020-01-01", periods=n)
    return pd.DataFrame(columns, index=index)


def _up(n: int, drift: float = 0.001) -> np.ndarray:
    return 100.0 * np.exp(drift * np.arange(n))


def _down(n: int, drift: float = 0.001) -> np.ndarray:
    return 100.0 * np.exp(-drift * np.arange(n))


def _fixed_book(*args, **kwargs) -> dict[str, float]:
    return {UNIVERSE[0]: 0.25, UNIVERSE[1]: 0.25}


def _closes(spy: np.ndarray, shv: np.ndarray | None = None, sectors_up: bool = True) -> pd.DataFrame:
    n = len(spy)
    cols = {t: (_up(n) if sectors_up else _down(n)) for t in UNIVERSE}
    cols["SPY"] = spy
    if shv is not None:
        cols["SHV"] = shv
    return _frame(cols)


def test_spy_above_sma_sector_book_plus_shv_residual(monkeypatch):
    monkeypatch.setattr(base, "compute_weights", _fixed_book)
    closes = _closes(_up(300))
    weights = compute_weights(closes, FAST, SLOW, CAP, off_scale=0.0)
    assert weights == {
        UNIVERSE[0]: 0.25,
        UNIVERSE[1]: 0.25,
        "SHV": pytest.approx(0.50),
    }
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert sum(weights.values()) == pytest.approx(1.0)


def test_spy_below_sma_zero_off_scale_parks_all_in_shv(monkeypatch):
    monkeypatch.setattr(base, "compute_weights", _fixed_book)
    closes = _closes(_down(300))
    assert compute_weights(closes, FAST, SLOW, CAP, off_scale=0.0) == {"SHV": 1.0}


def test_spy_below_sma_half_off_scale_parks_residual_in_shv(monkeypatch):
    monkeypatch.setattr(base, "compute_weights", _fixed_book)
    closes = _closes(_down(300))
    weights = compute_weights(closes, FAST, SLOW, CAP, off_scale=0.5)
    assert weights == {
        UNIVERSE[0]: pytest.approx(0.125),
        UNIVERSE[1]: pytest.approx(0.125),
        "SHV": pytest.approx(0.75),
    }


def test_cash_symbol_none_has_no_shv_key(monkeypatch):
    monkeypatch.setattr(base, "compute_weights", _fixed_book)
    closes_up = _closes(_up(300))
    weights_up = compute_weights(closes_up, FAST, SLOW, CAP, cash_symbol=None)
    assert "SHV" not in weights_up
    assert weights_up == {UNIVERSE[0]: 0.25, UNIVERSE[1]: 0.25}

    closes_down = _closes(_down(300))
    weights_down = compute_weights(closes_down, FAST, SLOW, CAP, off_scale=0.0, cash_symbol=None)
    assert weights_down == {}


def test_regime_window_changes_gate_on_crafted_series(monkeypatch):
    monkeypatch.setattr(base, "compute_weights", _fixed_book)
    # 150 bars at 200.0, then 100 bars linearly from 100.0 to 160.0.
    # Last price: 160.0.
    # 100-bar SMA: mean(linspace(100, 160, 100)) = 130.0 < 160.0 (SPY above 100-bar SMA).
    # 250-bar SMA: (150 * 200 + 100 * 130) / 250 = 172.0 > 160.0 (SPY below 250-bar SMA).
    part1 = np.full(150, 200.0)
    part2 = np.linspace(100.0, 160.0, 100)
    spy_crafted = np.concatenate([part1, part2])
    closes = _closes(spy_crafted)

    w100 = compute_weights(closes, FAST, SLOW, CAP, off_scale=0.0, regime_window=100)
    assert w100 == {UNIVERSE[0]: 0.25, UNIVERSE[1]: 0.25, "SHV": pytest.approx(0.50)}

    w250 = compute_weights(closes, FAST, SLOW, CAP, off_scale=0.0, regime_window=250)
    assert w250 == {"SHV": 1.0}


def test_shv_and_spy_columns_never_appear_as_sector_picks(monkeypatch):
    seen: dict[str, list[str]] = {}

    def _capture(closes, fast, slow, cap):
        seen["columns"] = list(closes.columns)
        return {}

    monkeypatch.setattr(base, "compute_weights", _capture)
    closes = _closes(_up(300), shv=_up(300))
    compute_weights(closes, FAST, SLOW, CAP)
    assert "SHV" not in seen["columns"]
    assert "SPY" not in seen["columns"]
    assert set(seen["columns"]) == set(UNIVERSE)


def test_short_sector_history_returns_empty_dict():
    closes = _closes(_up(50))
    assert compute_weights(closes, FAST, SLOW, CAP) == {}


def test_short_spy_history_fails_open(monkeypatch):
    monkeypatch.setattr(base, "compute_weights", _fixed_book)
    # Sector history (150) >= slow (100), but SPY history (150) < regime_window (200).
    closes = _closes(_down(150))
    weights = compute_weights(closes, fast=20, slow=100, cap=CAP, off_scale=0.0, regime_window=200)
    assert weights == {
        UNIVERSE[0]: 0.25,
        UNIVERSE[1]: 0.25,
        "SHV": pytest.approx(0.50),
    }


def test_missing_spy_column_fails_open(monkeypatch):
    monkeypatch.setattr(base, "compute_weights", _fixed_book)
    closes = _frame({t: _up(300) for t in UNIVERSE})
    weights = compute_weights(closes, FAST, SLOW, CAP, off_scale=0.0)
    assert weights == {
        UNIVERSE[0]: 0.25,
        UNIVERSE[1]: 0.25,
        "SHV": pytest.approx(0.50),
    }


def test_all_uptrending_unscaled_equal_weight_below_cap():
    n = 300
    closes = _closes(_up(n))
    weights = compute_weights(closes, FAST, SLOW, CAP)
    assert set(weights) == set(UNIVERSE)
    assert "SHV" not in weights
    for w in weights.values():
        assert w == pytest.approx(1 / 9)


def test_bot_class_metadata_and_construction(monkeypatch):
    assert QuantsmdSmaDailyGatedV3Bot.param_grid == {
        "regime_window": [100, 150, 200, 250],
        "off_scale": [0.0, 0.5],
    }
    assert QuantsmdSmaDailyGatedV3Bot.BACKTEST_PERIOD == "max"

    captured: dict = {}

    def fake_init(self, name, **kwargs):
        captured["name"] = name
        captured.update(kwargs)

    monkeypatch.setattr(Bot, "__init__", fake_init)
    bot = QuantsmdSmaDailyGatedV3Bot()
    assert captured["name"] == "QuantsmdSmaDailyGatedV3Bot"
    assert captured["tickers"] == [*UNIVERSE, "SPY", "SHV"]
    assert captured["benchmark_tickers"] == ["SPY"]
    assert captured["interval"] == "1d"
    assert captured["period"] == "2y"
    assert captured["regime_window"] == 200
    assert captured["cash_symbol"] == "SHV"
    assert bot.regime_window == 200
    assert bot.cash_symbol == "SHV"
