import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_stop_breakout_entry import (
    FRACTION,
    HOLD,
    LOOKBACK,
    UNIVERSE,
    QuantsmdStopBreakoutEntryBot,
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


def _bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """Build an open/high/low/close frame from explicit (o, h, l, c) rows."""
    arr = np.asarray(rows, dtype=float)
    index = pd.bdate_range("2020-01-01", periods=len(arr))
    return pd.DataFrame(arr, columns=["open", "high", "low", "close"], index=index)


def _flat(n: int) -> list[tuple[float, float, float, float]]:
    return [(100.0, 100.0, 100.0, 100.0)] * n


def _breakout() -> list[tuple[float, float, float, float]]:
    """A breakout bar: the high reaches the 100.2 stop set from the flat closes."""
    return [(100.0, 101.0, 100.0, 101.0)]


def test_history_shorter_than_lookback_returns_empty():
    assert compute_weights({"SPY": _bars(_flat(LOOKBACK))}) == {}


def test_breakout_fills_and_holds_at_last_bar():
    # 31 flat bars, then a bar whose high clears the 100.2 stop placed on bar 30.
    frame = _bars(_flat(31) + _breakout())
    weights = compute_weights({"SPY": frame})
    assert set(weights) == {"SPY"}
    assert weights["SPY"] == pytest.approx(FRACTION)


def test_flat_market_never_breaks_out_and_returns_empty():
    assert compute_weights({"SPY": _bars(_flat(120))}) == {}


def test_downtrend_never_breaks_out_and_returns_empty():
    closes = np.linspace(150.0, 100.0, 120)
    assert compute_weights({"SPY": _frame(closes)}) == {}


def test_time_stop_fires_on_last_bar_and_returns_empty():
    # The fill is on index 31, so the sell is on index 31 + HOLD = 41.
    rows = _flat(31) + _breakout() + [(101.0, 101.0, 101.0, 101.0)] * HOLD
    assert compute_weights({"SPY": _bars(rows)}) == {}


def test_held_one_bar_before_time_stop():
    rows = _flat(31) + _breakout() + [(101.0, 101.0, 101.0, 101.0)] * (HOLD - 1)
    weights = compute_weights({"SPY": _bars(rows)})
    assert weights == {"SPY": pytest.approx(FRACTION)}


def test_stop_order_expires_unfilled_and_returns_empty():
    # Every bar stays below the 100.2 stop, so nothing ever fills.
    assert compute_weights({"SPY": _bars(_flat(40))}) == {}


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
    assert QuantsmdStopBreakoutEntryBot.param_grid == {"hold": [5, 10, 20]}
    assert QuantsmdStopBreakoutEntryBot.BACKTEST_PERIOD == "max"
    assert LOOKBACK == 20
