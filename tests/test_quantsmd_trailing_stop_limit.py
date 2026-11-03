import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_trailing_stop_limit import (
    FAST,
    FRACTION,
    SLOW,
    UNIVERSE,
    QuantsmdTrailingStopLimitBot,
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
    trailing stop-limit is placed on the next bar, at close 100.8, so its trail
    is round(100.8 * 0.04, 2) = 4.03 and its limit offset is round(100.8 * 0.005, 2)
    = 0.5. The closes never reach the stop, so the position is held.
    """
    flat = np.full(60, 100.0)
    ramp = 100.0 + 0.4 * np.arange(1, 6)
    hold = np.full(30, 102.0)
    return np.concatenate([flat, ramp, hold])


def _rally_frame() -> pd.DataFrame:
    """The flip-then-hold series, then a climb from 102 to 120 by 1.0 a bar.

    The high water reaches 120, so the stop sits at 120 - 4.03 = 115.97 and its
    limit at 115.47.
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


def test_trailing_stop_limit_fills_on_last_bar_and_returns_empty():
    # The stop sits at 115.97. A low of 111 triggers it, and the open at 118 is
    # above it, so the fill is at the stop (115.97), which clears the 115.47 limit.
    frame = _rally_frame()
    frame.loc[frame.index[-1], ["open", "high", "low", "close"]] = [118.0, 118.0, 111.0, 112.0]
    assert compute_weights({"SPY": frame}) == {}


def test_trailing_stop_above_pullback_keeps_position():
    # A low of 116 stays above the 115.97 stop, so nothing triggers.
    frame = _rally_frame()
    frame.loc[frame.index[-1], ["open", "high", "low", "close"]] = [118.0, 118.0, 116.0, 117.0]
    weights = compute_weights({"SPY": frame})
    assert weights["SPY"] == pytest.approx(FRACTION)


def test_triggered_stop_limit_not_filled_stays_live():
    # Bar -2 gaps to 115.0 under the stop, triggers it, and its high of 115.4 is
    # below the 115.47 limit, so the stop-limit is live but unfilled. Bar -1 also
    # stays below the limit, so the position is held.
    frame = _rally_frame()
    frame.iloc[-2, frame.columns.get_loc("open")] = 115.0
    frame.iloc[-2, frame.columns.get_loc("high")] = 115.4
    frame.iloc[-2, frame.columns.get_loc("low")] = 114.0
    frame.iloc[-2, frame.columns.get_loc("close")] = 114.5
    frame.iloc[-1, frame.columns.get_loc("open")] = 114.5
    frame.iloc[-1, frame.columns.get_loc("high")] = 115.0
    frame.iloc[-1, frame.columns.get_loc("low")] = 114.0
    frame.iloc[-1, frame.columns.get_loc("close")] = 114.5
    weights = compute_weights({"SPY": frame})
    assert weights["SPY"] == pytest.approx(FRACTION)


def test_gap_through_stop_limit_fills_only_when_price_returns_to_limit():
    # Bar -2 gaps to 110, triggers the stop, and cannot fill at the open or on its
    # high of 111. Bar -1 trades up to 116, so the live limit at 115.47 fills.
    frame = _rally_frame()
    frame.iloc[-2, frame.columns.get_loc("open")] = 110.0
    frame.iloc[-2, frame.columns.get_loc("high")] = 111.0
    frame.iloc[-2, frame.columns.get_loc("low")] = 109.0
    frame.iloc[-2, frame.columns.get_loc("close")] = 110.0
    frame.iloc[-1, frame.columns.get_loc("open")] = 110.0
    frame.iloc[-1, frame.columns.get_loc("high")] = 116.0
    frame.iloc[-1, frame.columns.get_loc("low")] = 110.0
    frame.iloc[-1, frame.columns.get_loc("close")] = 115.0
    assert compute_weights({"SPY": frame}) == {}


def test_bearish_flip_sells_at_close_and_returns_empty():
    # The decline never reaches the 97.97 stop (102 high less 4.03), so only the
    # crossover can exit.
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
    assert QuantsmdTrailingStopLimitBot.param_grid == {
        "fast": [10, 20, 30],
        "slow": [50, 100, 200],
        "trail_pct": [0.03, 0.04, 0.05],
    }
    assert QuantsmdTrailingStopLimitBot.BACKTEST_PERIOD == "max"
    assert FAST < SLOW
