import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_oco_dip_or_breakout import (
    FRACTION,
    HOLD_BARS,
    OFFSET,
    UNIVERSE,
    QuantsmdOcoDipOrBreakoutBot,
    compute_weights,
)
from tradingbot.utils.quantsmd_common import ETF3

# 2024-01-01 is a Monday, so bar 0 of a frame starting here is a Monday.
MONDAY_START = "2024-01-01"
TUESDAY_START = "2024-01-02"


def _frame(closes: np.ndarray, start: str = MONDAY_START, spread: float = 0.0) -> pd.DataFrame:
    close = np.asarray(closes, dtype=float)
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    index = pd.bdate_range(start, periods=len(close))
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=index)


def _set_bar(frame: pd.DataFrame, pos: int, open_: float, high: float, low: float, close: float) -> None:
    frame.iloc[pos, frame.columns.get_loc("open")] = open_
    frame.iloc[pos, frame.columns.get_loc("high")] = high
    frame.iloc[pos, frame.columns.get_loc("low")] = low
    frame.iloc[pos, frame.columns.get_loc("close")] = close


def _dip_fill_frame(n_bars: int = 10) -> pd.DataFrame:
    """Flat at 100. Bar 0 is a Monday, so the OCO pair is placed at close 100:
    buy limit 98.00 and buy stop 102.00. Bar 1 dips to a low of 97.5, which fills
    the limit at 98.00. The position is then held, since the hold period is not up.
    """
    frame = _frame(np.full(n_bars, 100.0))
    _set_bar(frame, 1, open_=100.0, high=100.0, low=97.5, close=100.0)
    return frame


def test_single_bar_history_returns_empty():
    assert compute_weights({"SPY": _frame(np.array([100.0]))}) == {}


def test_dip_limit_fills_and_holds_at_last_bar():
    weights = compute_weights({"SPY": _dip_fill_frame(10)})
    assert weights == {"SPY": pytest.approx(FRACTION)}


def test_flat_market_never_fills_and_returns_empty():
    # Highs stay at 100, under the 102 buy stop, and lows stay at 100, above the
    # 98 buy limit. Each Monday re-posts the pair, and nothing ever fills.
    assert compute_weights({"SPY": _frame(np.full(30, 100.0))}) == {}


def test_hold_period_exit_fires_and_returns_empty():
    # Fill on bar 1 sets entry_bar = 1. Bar 11 is 10 bars later, so the market
    # sell fires at the close and the book is flat.
    frame = _dip_fill_frame(12)
    assert compute_weights({"SPY": frame}) == {}


def test_position_still_held_one_bar_before_hold_exit():
    # Bar 10 is 9 bars after the fill on bar 1, still under HOLD_BARS = 10.
    frame = _dip_fill_frame(11)
    assert compute_weights({"SPY": frame}) == {"SPY": pytest.approx(FRACTION)}


def test_breakout_stop_fills_and_holds():
    # Bar 1 rallies to a high of 102.5, which fills the buy stop at 102.00.
    frame = _frame(np.full(10, 100.0))
    _set_bar(frame, 1, open_=100.0, high=102.5, low=100.0, close=102.0)
    assert compute_weights({"SPY": frame}) == {"SPY": pytest.approx(FRACTION)}


def test_orders_are_only_placed_on_mondays():
    # Starting on a Tuesday, the first pair is placed on the next Monday (bar 4),
    # so the dip on bar 1 has no order to fill.
    frame = _frame(np.full(10, 100.0), start=TUESDAY_START)
    _set_bar(frame, 1, open_=100.0, high=100.0, low=97.5, close=100.0)
    assert compute_weights({"SPY": frame}) == {}


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
    assert QuantsmdOcoDipOrBreakoutBot.param_grid == {
        "offset": [0.01, 0.02, 0.03],
        "hold_bars": [5, 10, 20],
    }
    assert QuantsmdOcoDipOrBreakoutBot.BACKTEST_PERIOD == "max"
    assert OFFSET == 0.02
    assert HOLD_BARS == 10
