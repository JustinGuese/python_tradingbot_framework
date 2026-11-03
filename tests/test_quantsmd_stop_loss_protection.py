import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_stop_loss_protection import (
    FAST,
    FRACTION,
    SLOW,
    STOP_PCT,
    UNIVERSE,
    QuantsmdStopLossProtectionBot,
    compute_weights,
)
from tradingbot.utils.quantsmd_common import ETF3


def _frame(closes: np.ndarray, spread: float = 0.0) -> pd.DataFrame:
    close = np.asarray(closes, dtype=float)
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    index = pd.bdate_range("2020-01-01", periods=len(close))
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=index)


def _flip_then_hold() -> np.ndarray:
    """60 flat bars at 100, a 5-bar ramp to 102, then 30 bars at 102.

    SMA20 first rises above SMA50 on the first ramp bar (close 100.4), so the
    entry is at 100.4 and the protective stop is 95.38. Closes stay above it,
    so the position is held.
    """
    flat = np.full(60, 100.0)
    ramp = 100.0 + 0.4 * np.arange(1, 6)
    hold = np.full(30, 102.0)
    return np.concatenate([flat, ramp, hold])


def test_history_shorter_than_slow_returns_empty():
    closes = np.linspace(100.0, 110.0, SLOW - 1)
    assert compute_weights({"SPY": _frame(closes)}) == {}


def test_bullish_flip_places_stop_and_holds_at_last_bar():
    weights = compute_weights({"SPY": _frame(_flip_then_hold())})
    assert set(weights) == {"SPY"}
    assert weights["SPY"] == pytest.approx(FRACTION)


def test_flat_market_never_flips_and_returns_empty():
    ohlc = {"SPY": _frame(np.full(120, 100.0))}
    assert compute_weights(ohlc) == {}


def test_downtrend_never_opens_long():
    closes = np.linspace(150.0, 100.0, 120)
    assert compute_weights({"SPY": _frame(closes)}) == {}


def test_price_above_stop_on_last_bar_stays_held():
    # The stop is 95.38; a low of 96.0 on the last bar does not reach it.
    frame = _frame(_flip_then_hold())
    frame.loc[frame.index[-1], "low"] = 96.0
    weights = compute_weights({"SPY": frame})
    assert set(weights) == {"SPY"}
    assert weights["SPY"] == pytest.approx(FRACTION)


def test_stop_loss_fires_on_last_bar_and_returns_empty():
    frame = _frame(_flip_then_hold())
    frame.loc[frame.index[-1], "low"] = 90.0
    assert compute_weights({"SPY": frame}) == {}


def test_bearish_flip_sells_at_close_and_cancels_stop_returns_empty():
    # The decline stays above the 95.38 stop, so the stop never fills and only
    # the crossover can exit.
    closes = np.concatenate([_flip_then_hold(), np.linspace(102.0, 99.0, 20)])
    assert compute_weights({"SPY": _frame(closes)}) == {}


def test_missing_ticker_is_ignored():
    assert compute_weights({}) == {}


def test_random_walk_invariants():
    rng = np.random.default_rng(7)
    ohlc = {}
    for t in UNIVERSE:
        steps = rng.normal(0.0, 0.01, 600)
        closes = 100.0 * np.exp(np.cumsum(steps))
        ohlc[t] = _frame(closes, spread=0.5)
    weights = compute_weights(ohlc)
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert all(w >= 0 for w in weights.values())
    assert "USD" not in weights
    assert set(weights) <= set(UNIVERSE)


def test_bot_class_metadata_readable():
    assert UNIVERSE == ETF3
    assert QuantsmdStopLossProtectionBot.param_grid == {
        "fast": [10, 20, 30],
        "slow": [50, 100, 200],
        "stop_pct": [0.03, 0.05, 0.08],
    }
    assert QuantsmdStopLossProtectionBot.BACKTEST_PERIOD == "max"
    assert FAST < SLOW
    assert STOP_PCT == 0.05
