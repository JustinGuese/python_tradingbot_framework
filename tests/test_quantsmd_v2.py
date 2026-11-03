"""Tests for tradingbot/utils/quantsmd_v2.py (shared helpers for the v2 variants)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils.quantsmd_v2 import (
    min_hold_state,
    park_cash,
    regime_scale,
    scale_book,
    schmitt_state,
    vol_target_scale,
)


def _series(values: list[float]) -> pd.Series:
    return pd.Series(values, index=pd.bdate_range("2020-01-01", periods=len(values)), dtype=float)


def _alternating_closes(amplitude: float, n: int, ticker: str = "A") -> pd.DataFrame:
    """Close path whose daily returns alternate +amplitude / -amplitude."""
    steps = np.where(np.arange(n) % 2 == 0, amplitude, -amplitude)
    closes = 100.0 * np.cumprod(1.0 + steps)
    return pd.DataFrame({ticker: closes}, index=pd.bdate_range("2020-01-01", periods=n))


def _rising(n: int, ticker: str) -> pd.Series:
    return pd.Series(100.0 * (1.001 ** np.arange(n)), index=pd.bdate_range("2020-01-01", periods=n))


# --- schmitt_state -----------------------------------------------------------


def test_schmitt_starts_off_and_stays_on_between_thresholds() -> None:
    score = _series([0.1, 0.6, 0.2, 0.0, -0.6, 0.2])
    state = schmitt_state(score, enter_above=0.5, exit_below=-0.5)
    assert state.tolist() == [False, True, True, True, False, False]


def test_schmitt_returns_same_index() -> None:
    score = _series([0.6, 0.2])
    state = schmitt_state(score, enter_above=0.5, exit_below=-0.5)
    assert state.index.equals(score.index)
    assert state.dtype == bool


def test_schmitt_nan_resets_state() -> None:
    score = _series([0.6, np.nan, 0.2])
    state = schmitt_state(score, enter_above=0.5, exit_below=-0.5)
    assert state.tolist() == [True, False, False]


def test_schmitt_bad_thresholds_raise() -> None:
    with pytest.raises(ValueError):
        schmitt_state(_series([0.1]), enter_above=0.1, exit_below=0.2)


def test_schmitt_equal_thresholds_allowed() -> None:
    state = schmitt_state(_series([0.1, 0.0, -0.1]), enter_above=0.0, exit_below=0.0)
    assert state.tolist() == [True, True, False]


# --- min_hold_state ----------------------------------------------------------


def test_min_hold_suppresses_flip_inside_hold_window() -> None:
    raw = pd.Series([True, True, False, False, True, True, True, True])
    held = min_hold_state(raw, min_hold=3)
    # Flip to False at bar 2 is suppressed (only 2 bars since bar 0). Flip at
    # bar 3 is allowed. Flip back to True at bar 4 is suppressed until bar 6.
    assert held.tolist() == [True, True, True, False, False, False, True, True]


def test_min_hold_allows_flip_after_window() -> None:
    raw = pd.Series([False, True, True, True])
    held = min_hold_state(raw, min_hold=2)
    assert held.tolist() == [False, False, True, True]


def test_min_hold_first_bar_counts_as_flip() -> None:
    raw = pd.Series([False, True, False, False, False])
    held = min_hold_state(raw, min_hold=3)
    assert held.tolist() == [False, False, False, False, False]


def test_min_hold_one_is_identity() -> None:
    raw = pd.Series([True, False, True, False])
    held = min_hold_state(raw, min_hold=1)
    assert held.tolist() == raw.tolist()


def test_min_hold_empty_returns_empty() -> None:
    held = min_hold_state(pd.Series([], dtype=bool), min_hold=5)
    assert len(held) == 0


# --- vol_target_scale --------------------------------------------------------


def test_vol_target_low_vol_book_scales_up_to_max_gross() -> None:
    closes = _alternating_closes(0.001, 100)
    scaled = vol_target_scale({"A": 0.5}, closes, target_vol=0.10, window=63, max_gross=1.0)
    assert scaled["A"] == pytest.approx(1.0)


def test_vol_target_high_vol_book_scales_down() -> None:
    closes = _alternating_closes(0.05, 100)
    book = {"A": 1.0}
    scaled = vol_target_scale(book, closes, target_vol=0.10, window=63, max_gross=1.0)
    rets = closes["A"].pct_change().dropna().iloc[-63:]
    realised = float(rets.std() * math.sqrt(252))
    assert scaled["A"] == pytest.approx(0.10 / realised)
    assert 0.0 < scaled["A"] < 1.0


def test_vol_target_short_history_unchanged() -> None:
    closes = _alternating_closes(0.01, 30)
    book = {"A": 0.5}
    assert vol_target_scale(book, closes, target_vol=0.10) == book


def test_vol_target_empty_book_returns_empty() -> None:
    closes = _alternating_closes(0.01, 100)
    assert vol_target_scale({}, closes, target_vol=0.10) == {}


def test_vol_target_ignores_tickers_missing_from_closes() -> None:
    closes = _alternating_closes(0.05, 100, ticker="A")
    scaled = vol_target_scale({"A": 1.0, "ZZZ": 0.5}, closes, target_vol=0.10)
    assert set(scaled) == {"A", "ZZZ"}
    assert scaled["ZZZ"] == pytest.approx(0.5 * scaled["A"])


# --- park_cash ---------------------------------------------------------------


def test_park_cash_residual_goes_to_shv() -> None:
    assert park_cash({"A": 0.6}) == pytest.approx({"A": 0.6, "SHV": 0.4})


def test_park_cash_full_book_unchanged() -> None:
    book = {"A": 0.5, "B": 0.5}
    assert park_cash(book) == book
    assert park_cash(book) is not book


def test_park_cash_ignores_small_residual() -> None:
    assert park_cash({"A": 0.996}) == {"A": 0.996}


def test_park_cash_never_adds_negative_weight() -> None:
    assert "SHV" not in park_cash({"A": 1.2})


def test_park_cash_custom_symbol() -> None:
    assert park_cash({"A": 0.5}, symbol="BIL") == pytest.approx({"A": 0.5, "BIL": 0.5})


# --- regime_scale ------------------------------------------------------------


def test_regime_above_sma_returns_one() -> None:
    closes = pd.DataFrame({"SPY": _rising(300, "SPY")})
    assert regime_scale(closes) == 1.0


def test_regime_below_sma_returns_off_scale() -> None:
    falling = pd.Series(100.0 * (0.999 ** np.arange(300)))
    closes = pd.DataFrame({"SPY": falling})
    assert regime_scale(closes, off_scale=0.5) == 0.5
    assert regime_scale(closes) == 0.0


def test_regime_missing_ref_fails_open() -> None:
    closes = pd.DataFrame({"QQQ": _rising(300, "QQQ")})
    assert regime_scale(closes, ref="SPY", off_scale=0.0) == 1.0


def test_regime_short_history_fails_open() -> None:
    falling = pd.Series(100.0 * (0.9 ** np.arange(50)))
    closes = pd.DataFrame({"SPY": falling})
    assert regime_scale(closes, off_scale=0.0) == 1.0


# --- scale_book --------------------------------------------------------------


def test_scale_book_halves_weights() -> None:
    assert scale_book({"A": 0.4, "B": 0.6}, 0.5) == pytest.approx({"A": 0.2, "B": 0.3})


def test_scale_book_zero_factor_returns_empty() -> None:
    assert scale_book({"A": 0.4, "B": 0.6}, 0.0) == {}
