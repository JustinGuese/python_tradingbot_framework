import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_limit_trend_pullback import (
    FRACTION,
    SMA_WINDOW,
    UNIVERSE,
    QuantsmdLimitTrendPullbackBot,
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


def _uptrend_prefix() -> np.ndarray:
    """60 bars rising from 90 to 100.

    The last close of 100 is above its 50-bar average (about 95.85), so a buy
    limit is posted at round(100 * 0.99, 2) = 99.00 for the next bar.
    """
    return np.linspace(90.0, 100.0, 60)


def _pullback_fill_hold() -> np.ndarray:
    """A dip to 98.5 fills the 99.00 buy limit at 99.00 and the position is held.

    The average stays below 98.5 through the hold, so no market sell fires.
    The sell limit round(99.00 * 1.04, 2) = 102.96 is never reached.
    """
    return np.concatenate([_uptrend_prefix(), np.full(11, 98.5)])


def test_history_shorter_than_sma_returns_empty():
    closes = np.linspace(90.0, 100.0, SMA_WINDOW - 1)
    assert compute_weights({"SPY": _frame(closes)}) == {}


def test_pullback_fills_limit_and_holds_at_last_bar():
    weights = compute_weights({"SPY": _frame(_pullback_fill_hold())})
    assert weights == {"SPY": pytest.approx(FRACTION)}


def test_limit_never_fills_and_returns_empty():
    # Flat at 100 keeps the low above the 99.00 buy limit, so nothing fills.
    closes = np.concatenate([_uptrend_prefix(), np.full(5, 100.0)])
    assert compute_weights({"SPY": _frame(closes)}) == {}


def test_close_below_average_sells_at_close_and_returns_empty():
    # The close of 80 is far below the average (about 97), so the held position
    # is sold at the close and the sell limit is cancelled.
    closes = np.concatenate([_pullback_fill_hold(), [80.0]])
    assert compute_weights({"SPY": _frame(closes)}) == {}


def test_falling_market_never_buys_and_returns_empty():
    closes = np.linspace(100.0, 80.0, 80)
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
    assert QuantsmdLimitTrendPullbackBot.param_grid == {
        "sma_window": [20, 50, 100],
    }
    assert QuantsmdLimitTrendPullbackBot.BACKTEST_PERIOD == "max"
    assert SMA_WINDOW == 50
