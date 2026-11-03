"""Tests for the quants.md daily partial-drift bot (pure compute_weights, synthetic data)."""

import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_daily_partial_drift import (
    CAP,
    LOOKBACK,
    UNIVERSE,
    QuantsmdDailyPartialDriftBot,
    compute_weights,
)
from tradingbot.utils.quantsmd_common import SECTORS9


def _frame(columns: dict[str, np.ndarray]) -> pd.DataFrame:
    n = len(next(iter(columns.values())))
    index = pd.bdate_range("2020-01-01", periods=n)
    return pd.DataFrame(columns, index=index)


def _momentum_frame(moms: dict[str, float], n: int = 100) -> pd.DataFrame:
    """Flat 100 closes, with the last bar set so each ticker's momentum equals its target.

    The bar ``lookback`` back is still 100 for any lookback below ``n - 1``, so the
    trailing return over the default lookback is exactly the value in ``moms``.
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


def test_three_equal_positive_names_get_one_third_each():
    closes = _momentum_frame({"XLB": 0.10, "XLE": 0.10, "XLF": 0.10})
    weights = compute_weights(closes, LOOKBACK, CAP)
    assert set(weights) == {"XLB", "XLE", "XLF"}
    for w in weights.values():
        assert w == pytest.approx(1 / 3)


def test_three_positive_names_with_one_large_are_clipped_and_cash_left():
    closes = _momentum_frame({"XLB": 0.1, "XLE": 0.1, "XLF": 0.8})
    weights = compute_weights(closes, LOOKBACK, CAP)
    assert weights["XLB"] == pytest.approx(0.1)
    assert weights["XLE"] == pytest.approx(0.1)
    assert weights["XLF"] == pytest.approx(0.35)
    assert sum(weights.values()) == pytest.approx(0.55)


def test_all_negative_momentum_returns_empty():
    closes = _momentum_frame(dict.fromkeys(SECTORS9, -0.05))
    assert compute_weights(closes, LOOKBACK, CAP) == {}


def test_zero_momentum_is_not_held():
    closes = _momentum_frame({"XLB": 0.0, "XLE": 0.0})
    assert compute_weights(closes, LOOKBACK, CAP) == {}


def test_63_rows_returns_empty():
    closes = _momentum_frame({"XLB": 0.1}, n=63)
    assert compute_weights(closes, LOOKBACK, CAP) == {}


def test_generic_weights_nonnegative_and_bounded():
    rng = np.random.default_rng(11)
    n = 600
    cols = {}
    for t in UNIVERSE:
        steps = rng.normal(0.0002, 0.01, n)
        cols[t] = 100.0 * np.exp(np.cumsum(steps))
    closes = _frame(cols)
    weights = compute_weights(closes, LOOKBACK, CAP)
    assert all(w >= 0 for w in weights.values())
    assert all(w <= CAP + 1e-12 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights
    assert set(weights) <= set(UNIVERSE)


def test_generic_too_short_history_never_raises():
    closes = _frame({t: 100.0 * np.exp(0.001 * np.arange(10)) for t in UNIVERSE})
    assert compute_weights(closes, LOOKBACK, CAP) == {}


def test_bot_class_metadata_readable():
    assert QuantsmdDailyPartialDriftBot.BACKTEST_PERIOD == "max"
    assert QuantsmdDailyPartialDriftBot.param_grid == {"lookback": [21, 63, 126]}
    assert list(SECTORS9) == UNIVERSE
