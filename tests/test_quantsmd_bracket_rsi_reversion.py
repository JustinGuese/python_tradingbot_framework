import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_bracket_rsi_reversion import (
    FRACTION,
    UNIVERSE,
    QuantsmdBracketRsiReversionBot,
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


def _drop_then_hold() -> np.ndarray:
    """30 bars rising 0.5 a bar to 114.5, 20 flat bars, one 20-point drop, 20 flat bars.

    The drop bar's RSI falls below 35 and the bot buys at its close, 94.5. The
    bracket is 98.28 / 92.61. The flat bars after it keep RSI low and stay inside
    the bracket, so the position is held at the last bar.
    """
    rally = 100.0 + 0.5 * np.arange(30)
    top = rally[-1]
    flat_top = np.full(20, top)
    low_level = top - 20.0
    drop = np.full(1, low_level)
    hold = np.full(20, low_level)
    return np.concatenate([rally, flat_top, drop, hold])


def _rising(n: int = 80) -> np.ndarray:
    return 100.0 + 0.5 * np.arange(n)


def test_history_shorter_than_rsi_warmup_returns_empty():
    closes = np.linspace(110.0, 100.0, 10)
    assert compute_weights({"SPY": _frame(closes)}) == {}


def test_rsi_dip_buys_and_holds_at_last_bar():
    weights = compute_weights({"SPY": _frame(_drop_then_hold())})
    assert set(weights) == {"SPY"}
    assert weights["SPY"] == pytest.approx(FRACTION)


def test_rising_market_never_buys_and_returns_empty():
    assert compute_weights({"SPY": _frame(_rising())}) == {}


def test_missing_ticker_is_ignored():
    assert compute_weights({}) == {}


# The exit bars close back above 35. Otherwise a flat bot whose RSI is still low
# re-buys at that same close, as the replay order (a) exit then (b) decision implies.


def test_stop_loss_fires_on_last_bar_and_returns_empty():
    frame = _frame(_drop_then_hold())
    frame.loc[frame.index[-1], ["high", "low", "close"]] = [100.0, 90.0, 100.0]
    assert compute_weights({"SPY": frame}) == {}


def test_take_profit_fires_on_last_bar_and_returns_empty():
    frame = _frame(_drop_then_hold())
    frame.loc[frame.index[-1], ["high", "low", "close"]] = [105.0, 94.5, 105.0]
    assert compute_weights({"SPY": frame}) == {}


def test_only_the_dipping_ticker_is_held():
    weights = compute_weights({"SPY": _frame(_drop_then_hold()), "QQQ": _frame(_rising())})
    assert set(weights) == {"SPY"}
    assert weights["SPY"] == pytest.approx(FRACTION)


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
    assert QuantsmdBracketRsiReversionBot.param_grid == {
        "buy_below": [30, 35, 40],
        "sell_above": [65, 70, 75],
    }
    assert QuantsmdBracketRsiReversionBot.BACKTEST_PERIOD == "max"
