import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_trailing_stop_rsi import (
    BUY_BELOW,
    FRACTION,
    RSI_WINDOW,
    SELL_ABOVE,
    UNIVERSE,
    QuantsmdTrailingStopRsiBot,
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


def _dip_then_hold() -> np.ndarray:
    """A slow drift from 100 down to 96 over 40 bars, then 30 flat bars at 96.

    The RSI is defined from the 14th bar and is 0 through the drift, so the bot
    buys at the close of bar 13 (98.6). The trailing stop is placed on bar 14
    with high water 98.5, so it sits near 93.6. The closes never reach it, and
    the flat tail leaves the RSI low, so the position is held.
    """
    drift = np.linspace(100.0, 96.0, 41)[1:]
    return np.concatenate([drift, np.full(30, 96.0)])


def test_history_shorter_than_rsi_window_returns_empty():
    # The RSI is first defined on the 14th bar, so 13 bars leave it all NaN.
    closes = np.linspace(100.0, 90.0, RSI_WINDOW - 1)
    assert compute_weights({"SPY": _frame(closes)}) == {}


def test_oversold_dip_opens_position_and_holds_at_last_bar():
    weights = compute_weights({"SPY": _frame(_dip_then_hold())})
    assert set(weights) == {"SPY"}
    assert weights["SPY"] == pytest.approx(FRACTION)


def test_rising_market_never_buys_and_returns_empty():
    closes = np.linspace(100.0, 150.0, 120)
    assert compute_weights({"SPY": _frame(closes)}) == {}


def test_flat_market_never_buys_and_returns_empty():
    ohlc = {"SPY": _frame(np.full(120, 100.0))}
    assert compute_weights(ohlc) == {}


def test_rsi_above_exit_level_sells_at_close_and_returns_empty():
    # A sharp climb from the 96 base pushes the RSI above 65 while the stop
    # is still far below the closes, so the exit is the RSI rule.
    rally = np.linspace(97.0, 110.0, 14)
    closes = np.concatenate([_dip_then_hold(), rally])
    assert compute_weights({"SPY": _frame(closes)}) == {}


def test_trailing_stop_fires_on_last_bar_and_returns_empty():
    # The stop is near 93.6. A low of 90 fills it at min(96, stop). The bar
    # closes back at 97, so the RSI is above 38 and the bot does not re-buy.
    frame = _frame(_dip_then_hold())
    frame.loc[frame.index[-1], ["open", "high", "low", "close"]] = [96.0, 97.0, 90.0, 97.0]
    assert compute_weights({"SPY": frame}) == {}


def test_stop_out_with_oversold_rsi_rebuys_at_close():
    # Same stop-out, but the bar closes at 91 with the RSI still near 0. The
    # bot is flat after the fill, and the oversold rule buys again at the close.
    frame = _frame(_dip_then_hold())
    frame.loc[frame.index[-1], ["open", "high", "low", "close"]] = [96.0, 96.0, 90.0, 91.0]
    weights = compute_weights({"SPY": frame})
    assert weights["SPY"] == pytest.approx(FRACTION)


def test_missing_ticker_is_ignored():
    assert compute_weights({}) == {}


def test_random_walk_invariants():
    rng = np.random.default_rng(11)
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
    assert QuantsmdTrailingStopRsiBot.param_grid == {
        "buy_below": [30.0, 38.0, 45.0],
        "sell_above": [60.0, 65.0, 70.0],
        "trail_pct": [0.03, 0.05, 0.07],
    }
    assert QuantsmdTrailingStopRsiBot.BACKTEST_PERIOD == "max"
    assert BUY_BELOW < SELL_ABOVE
