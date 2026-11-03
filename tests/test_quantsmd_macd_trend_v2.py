"""Tests for the quants.md MACD trend v2 bot (pure compute_weights, synthetic data)."""

import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_macd_trend import (
    FAST as ORIG_FAST,
)
from tradingbot.quantsmd_macd_trend import (
    SIGNAL as ORIG_SIGNAL,
)
from tradingbot.quantsmd_macd_trend import (
    SLOW as ORIG_SLOW,
)
from tradingbot.quantsmd_macd_trend import (
    compute_weights as original_compute_weights,
)
from tradingbot.quantsmd_macd_trend_v2 import (
    CAP,
    FAST,
    SIGNAL,
    SLOW,
    UNIVERSE,
    QuantsmdMacdTrendV2Bot,
    compute_weights,
)

# Steady trends this strong sit clearly above the +0.002 entry band on the
# close-normalised histogram. A gentle 0.1%/bar drift never reaches it.
DRIFT = 0.02


def _frame(columns: dict[str, np.ndarray]) -> pd.DataFrame:
    n = len(next(iter(columns.values())))
    index = pd.bdate_range("2020-01-01", periods=n)
    return pd.DataFrame(columns, index=index)


def _up(n: int, drift: float = DRIFT) -> np.ndarray:
    return 100.0 * np.exp(drift * np.arange(n))


def _rise_then_slide(n_rise: int, n_slide: int, slide: float = -0.03) -> np.ndarray:
    # A steady rise, then a sharp slide over the last ``n_slide`` bars. The slide
    # drives the normalised histogram well below -BAND, so the state turns off.
    base = _up(n_rise)
    tail = base[-1] * np.exp(slide * np.arange(1, n_slide + 1))
    return np.concatenate([base, tail])


def test_original_constants_match_v2():
    # The v2 bot must fit the same MACD windows as the source it varies.
    assert (FAST, SLOW, SIGNAL) == (ORIG_FAST, ORIG_SLOW, ORIG_SIGNAL)


def test_all_uptrending_equal_weight_below_cap():
    n = 300
    closes = _frame({t: _up(n) for t in UNIVERSE})
    weights = compute_weights(closes, FAST, SLOW, SIGNAL)
    assert set(weights) == set(UNIVERSE)
    for w in weights.values():
        assert w == pytest.approx(1 / 9)
        assert w < CAP


def test_one_bar_dip_inside_band_does_not_exit():
    # The last bar drops 12%. That pushes the normalised histogram to about
    # -0.0016, inside the band, so the hysteresis keeps XLB. The source has
    # already sold XLB, because MACD is below its signal line.
    n = 300
    dipped = _up(n)
    dipped[-1] = dipped[-2] * 0.88
    closes = _frame({"XLB": dipped, **{t: _up(n) for t in UNIVERSE if t != "XLB"}})
    assert "XLB" not in original_compute_weights(closes, ORIG_FAST, ORIG_SLOW, ORIG_SIGNAL)
    weights = compute_weights(closes, FAST, SLOW, SIGNAL)
    assert "XLB" in weights
    assert weights["XLB"] == pytest.approx(1 / 9)


def test_sustained_crash_exits():
    rise = 200
    closes_full = _frame(
        {
            "XLB": _rise_then_slide(rise, 30),
            **{t: _up(rise + 30) for t in UNIVERSE if t != "XLB"},
        }
    )
    # Held through the rise, dropped once the slide has lasted.
    held_before = compute_weights(closes_full.iloc[:rise], FAST, SLOW, SIGNAL)
    assert "XLB" in held_before
    weights = compute_weights(closes_full, FAST, SLOW, SIGNAL)
    assert "XLB" not in weights
    assert set(weights) == set(UNIVERSE) - {"XLB"}


def test_all_sliding_returns_empty():
    n_rise, n_slide = 200, 30
    closes = _frame({t: _rise_then_slide(n_rise, n_slide) for t in UNIVERSE})
    assert compute_weights(closes, FAST, SLOW, SIGNAL) == {}


def test_34_rows_returns_empty():
    closes = _frame({t: _up(34) for t in UNIVERSE})
    assert compute_weights(closes, FAST, SLOW, SIGNAL) == {}


def test_flat_series_is_not_active():
    # MACD and its signal are both zero on a constant price, so the histogram
    # never clears the entry band.
    closes = _frame({t: np.full(100, 50.0) for t in UNIVERSE})
    assert compute_weights(closes, FAST, SLOW, SIGNAL) == {}


def test_generic_weights_nonnegative_and_bounded():
    rng = np.random.default_rng(7)
    n = 600
    cols = {}
    for t in UNIVERSE:
        steps = rng.normal(0.01, 0.01, n)
        cols[t] = 100.0 * np.exp(np.cumsum(steps))
    closes = _frame(cols)
    weights = compute_weights(closes, FAST, SLOW, SIGNAL)
    assert all(w >= 0 for w in weights.values())
    assert all(w <= CAP + 1e-12 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights
    assert set(weights) <= set(UNIVERSE)


def test_generic_too_short_history_never_raises():
    closes = _frame({t: _up(10) for t in UNIVERSE})
    assert compute_weights(closes, FAST, SLOW, SIGNAL) == {}


def test_generic_bot_class_metadata_readable():
    assert QuantsmdMacdTrendV2Bot.param_grid == {
        "band": [0.001, 0.002, 0.004],
        "min_hold": [5, 10, 21],
    }
