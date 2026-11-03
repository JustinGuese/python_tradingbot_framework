import numpy as np
import pandas as pd
import pytest

from tradingbot import quantsmd_sma_daily as base
from tradingbot.quantsmd_sma_daily_gated import (
    CAP,
    FAST,
    SLOW,
    UNIVERSE,
    QuantsmdSmaDailyGatedBot,
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


def _fixed_book(*args, **kwargs) -> dict[str, float]:
    return {UNIVERSE[0]: 0.25, UNIVERSE[1]: 0.25}


def _closes_with_spy(spy: np.ndarray, sectors_up: bool = True) -> pd.DataFrame:
    n = len(spy)
    cols = {t: (_up(n) if sectors_up else _down(n)) for t in UNIVERSE}
    cols["SPY"] = spy
    return _frame(cols)


def test_spy_above_sma200_book_equals_original(monkeypatch):
    monkeypatch.setattr(base, "compute_weights", _fixed_book)
    closes = _closes_with_spy(_up(300))
    assert compute_weights(closes, FAST, SLOW, CAP, off_scale=0.0) == {
        UNIVERSE[0]: 0.25,
        UNIVERSE[1]: 0.25,
    }


def test_spy_below_sma200_goes_to_cash_with_zero_off_scale(monkeypatch):
    monkeypatch.setattr(base, "compute_weights", _fixed_book)
    closes = _closes_with_spy(_down(300))
    assert compute_weights(closes, FAST, SLOW, CAP, off_scale=0.0) == {}


def test_spy_below_sma200_halves_book_with_half_off_scale(monkeypatch):
    monkeypatch.setattr(base, "compute_weights", _fixed_book)
    closes = _closes_with_spy(_down(300))
    weights = compute_weights(closes, FAST, SLOW, CAP, off_scale=0.5)
    assert weights == {UNIVERSE[0]: pytest.approx(0.125), UNIVERSE[1]: pytest.approx(0.125)}


def test_spy_is_never_a_sector_candidate(monkeypatch):
    seen: dict[str, list[str]] = {}

    def _spy_capture(closes, fast, slow, cap):
        seen["columns"] = list(closes.columns)
        return {}

    monkeypatch.setattr(base, "compute_weights", _spy_capture)
    compute_weights(_closes_with_spy(_up(300)), FAST, SLOW, CAP)
    assert "SPY" not in seen["columns"]
    assert set(seen["columns"]) == set(UNIVERSE)


def test_short_spy_history_fails_open(monkeypatch):
    monkeypatch.setattr(base, "compute_weights", _fixed_book)
    closes = _closes_with_spy(_down(150))
    weights = compute_weights(closes, FAST, SLOW, CAP, off_scale=0.0)
    assert weights == {UNIVERSE[0]: 0.25, UNIVERSE[1]: 0.25}


def test_missing_spy_column_fails_open(monkeypatch):
    monkeypatch.setattr(base, "compute_weights", _fixed_book)
    closes = _frame({t: _up(300) for t in UNIVERSE})
    weights = compute_weights(closes, FAST, SLOW, CAP, off_scale=0.0)
    assert weights == {UNIVERSE[0]: 0.25, UNIVERSE[1]: 0.25}


def test_all_uptrending_unscaled_equal_weight_below_cap():
    n = 300
    closes = _closes_with_spy(_up(n))
    weights = compute_weights(closes, FAST, SLOW, CAP)
    assert set(weights) == set(UNIVERSE)
    for w in weights.values():
        assert w == pytest.approx(1 / 9)


def test_generic_bot_class_metadata_readable():
    assert QuantsmdSmaDailyGatedBot.param_grid == {"off_scale": [0.0, 0.5]}
    assert QuantsmdSmaDailyGatedBot.BACKTEST_PERIOD == "max"
