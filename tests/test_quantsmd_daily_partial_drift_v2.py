"""Tests for the quants.md daily partial-drift v2 bot (pure compute_weights, synthetic data)."""

import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_daily_partial_drift import compute_weights as original_compute_weights
from tradingbot.quantsmd_daily_partial_drift_v2 import (
    CAP,
    ENTER,
    MIN_HOLD,
    UNIVERSE,
    QuantsmdDailyPartialDriftV2Bot,
    compute_weights,
)
from tradingbot.utils.quantsmd_common import SECTORS9

# The scenarios below are sized for a 63-bar window; the shipped default is 126 (walk-forward 2026-11).
LOOKBACK = 63

# A steady 0.2%/bar rise gives a 63-bar momentum of about +13%, well above the
# +0.02 entry threshold.
RISE = 0.002


def _frame(columns: dict[str, np.ndarray]) -> pd.DataFrame:
    n = len(next(iter(columns.values())))
    index = pd.bdate_range("2020-01-01", periods=n)
    return pd.DataFrame(columns, index=index)


def _up(n: int, drift: float = RISE) -> np.ndarray:
    return 100.0 * np.exp(drift * np.arange(n))


def _momentum_frame(moms: dict[str, float], n: int = 100) -> pd.DataFrame:
    """Flat 100 closes, with the last bar set so each ticker's momentum equals its target.

    Names not listed get a last-bar momentum of -1%, which is below zero and below
    the entry threshold, so they are never held.
    """
    cols: dict[str, np.ndarray] = {}
    for ticker in SECTORS9:
        values = np.full(n, 100.0)
        if ticker in moms:
            values[-1] = 100.0 * (1.0 + moms[ticker])
        else:
            values[-1] = 100.0 * 0.99
        cols[ticker] = values
    return _frame(cols)


def test_all_uptrending_equal_weight_below_cap():
    n = 200
    closes = _frame({t: _up(n) for t in UNIVERSE})
    weights = compute_weights(closes, LOOKBACK, CAP)
    assert set(weights) == set(UNIVERSE)
    for w in weights.values():
        assert w == pytest.approx(1 / 9)
        assert w < CAP


def test_three_positive_names_with_one_large_are_clipped_and_cash_left():
    closes = _momentum_frame({"XLB": 0.1, "XLE": 0.1, "XLF": 0.8})
    weights = compute_weights(closes, LOOKBACK, CAP)
    assert weights["XLB"] == pytest.approx(0.1)
    assert weights["XLE"] == pytest.approx(0.1)
    assert weights["XLF"] == pytest.approx(CAP)
    assert sum(weights.values()) == pytest.approx(0.55)


def test_one_bar_dip_inside_band_does_not_exit():
    # Rise for 200 bars, then one bar that puts the 63-bar momentum at -1%.
    # That is below zero, so the source drops XLB, but above -ENTER, so the
    # hysteresis keeps it at the floor score.
    n = 200
    dipped = _up(n)
    dipped[-1] = dipped[-64] * 0.99
    closes = _frame({"XLB": dipped, **{t: _up(n) for t in UNIVERSE if t != "XLB"}})
    assert "XLB" not in original_compute_weights(closes, LOOKBACK, CAP)
    weights = compute_weights(closes, LOOKBACK, CAP)
    assert "XLB" in weights
    assert 0.0 < weights["XLB"] < 0.01


def test_below_entry_threshold_is_not_held():
    # A 1% trailing gain is positive, so the source holds it. It never clears
    # the +0.02 entry threshold, so v2 does not.
    n = 200
    slow = _up(n, drift=np.log(1.01) / LOOKBACK)
    closes = _frame({"XLB": slow, **{t: _up(n) for t in UNIVERSE if t != "XLB"}})
    assert "XLB" in original_compute_weights(closes, LOOKBACK, CAP)
    assert "XLB" not in compute_weights(closes, LOOKBACK, CAP)


def test_sustained_crash_exits():
    rise, slide = 150, 80
    base = _up(rise)
    tail = base[-1] * np.exp(-0.005 * np.arange(1, slide + 1))
    crashed = np.concatenate([base, tail])
    closes = _frame({"XLB": crashed, **{t: _up(rise + slide) for t in UNIVERSE if t != "XLB"}})
    # Held through the rise, dropped once the momentum has stayed below -ENTER.
    assert "XLB" in compute_weights(closes.iloc[:rise], LOOKBACK, CAP)
    weights = compute_weights(closes, LOOKBACK, CAP)
    assert "XLB" not in weights
    assert set(weights) == set(UNIVERSE) - {"XLB"}


def test_all_negative_momentum_returns_empty():
    closes = _momentum_frame(dict.fromkeys(SECTORS9, -0.05))
    assert compute_weights(closes, LOOKBACK, CAP) == {}


def test_63_rows_returns_empty():
    closes = _momentum_frame({"XLB": 0.1}, n=63)
    assert compute_weights(closes, LOOKBACK, CAP) == {}


def test_generic_weights_nonnegative_and_bounded():
    rng = np.random.default_rng(11)
    n = 600
    cols = {}
    for t in UNIVERSE:
        steps = rng.normal(0.002, 0.01, n)
        cols[t] = 100.0 * np.exp(np.cumsum(steps))
    closes = _frame(cols)
    weights = compute_weights(closes, LOOKBACK, CAP)
    assert all(w >= 0 for w in weights.values())
    assert all(w <= CAP + 1e-12 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights
    assert set(weights) <= set(UNIVERSE)


def test_bot_class_metadata_readable():
    assert QuantsmdDailyPartialDriftV2Bot.BACKTEST_PERIOD == "max"
    assert QuantsmdDailyPartialDriftV2Bot.param_grid == {"lookback": [21, 63, 126]}
    assert ENTER == 0.02
    assert MIN_HOLD == 10
    assert list(SECTORS9) == UNIVERSE
