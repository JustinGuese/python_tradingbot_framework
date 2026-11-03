import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_trailing_stop_trend import (
    FAST,
    FRACTION,
    SLOW,
    UNIVERSE,
    QuantsmdTrailingStopTrendBot,
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

    SMA20 first rises above SMA50 on the first ramp bar (close 100.4). The
    trailing stop is placed on the next bar, with high water 100.8. The closes
    never fall far enough to reach it, so the position is held.
    """
    flat = np.full(60, 100.0)
    ramp = 100.0 + 0.4 * np.arange(1, 6)
    hold = np.full(30, 102.0)
    return np.concatenate([flat, ramp, hold])


def _rally_frame() -> pd.DataFrame:
    """The flip-then-hold series, then a climb from 102 to 120 by 1.0 a bar.

    The stop ratchets up with the highs, so at 120 the stop sits at 115.2.
    """
    climb = np.linspace(102.0, 120.0, 19)[1:]
    return _frame(np.concatenate([_flip_then_hold(), climb]))


def test_history_shorter_than_slow_returns_empty():
    closes = np.linspace(100.0, 110.0, SLOW - 1)
    assert compute_weights({"SPY": _frame(closes)}) == {}


def test_bullish_flip_opens_position_and_holds_at_last_bar():
    weights = compute_weights({"SPY": _frame(_flip_then_hold())})
    assert set(weights) == {"SPY"}
    assert weights["SPY"] == pytest.approx(FRACTION)


def test_flat_market_never_flips_and_returns_empty():
    ohlc = {"SPY": _frame(np.full(120, 100.0))}
    assert compute_weights(ohlc) == {}


def test_downtrend_never_opens_long():
    closes = np.linspace(150.0, 100.0, 120)
    assert compute_weights({"SPY": _frame(closes)}) == {}


def test_trailing_stop_fires_on_last_bar_and_returns_empty():
    # The stop sits at 115.2 (4% below the 120 high). A low of 111 reaches it,
    # and the open at 118 is above it, so the fill is at 115.2.
    frame = _rally_frame()
    frame.loc[frame.index[-1], ["open", "high", "low", "close"]] = [118.0, 118.0, 111.0, 112.0]
    assert compute_weights({"SPY": frame}) == {}


def test_trailing_stop_above_pullback_keeps_position():
    # A low of 116 stays above the 115.2 stop that the 120 high set.
    frame = _rally_frame()
    frame.loc[frame.index[-1], ["open", "high", "low", "close"]] = [118.0, 118.0, 116.0, 117.0]
    weights = compute_weights({"SPY": frame})
    assert weights["SPY"] == pytest.approx(FRACTION)


def test_gap_below_trailing_stop_fills_at_open():
    # The open at 110 is already under the 115.2 stop, so the fill is at the open.
    frame = _rally_frame()
    frame.loc[frame.index[-1], ["open", "high", "low", "close"]] = [110.0, 111.0, 109.0, 110.0]
    assert compute_weights({"SPY": frame}) == {}


def test_bearish_flip_sells_at_close_and_returns_empty():
    # The decline stays above the 97.92 trailing stop (4% below the 102 high),
    # so only the crossover can exit.
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
    assert QuantsmdTrailingStopTrendBot.param_grid == {
        "fast": [10, 20, 30],
        "slow": [50, 100, 200],
        "trail_pct": [0.03, 0.04, 0.05],
    }
    assert QuantsmdTrailingStopTrendBot.BACKTEST_PERIOD == "max"
    assert FAST < SLOW
