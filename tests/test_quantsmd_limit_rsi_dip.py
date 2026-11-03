import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_limit_rsi_dip import (
    BUY_RSI,
    FRACTION,
    UNIVERSE,
    QuantsmdLimitRsiDipBot,
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


def _dip_prefix() -> np.ndarray:
    """30 bars alternating 100 / 101 so RSI sits near 50, then a drop to 96.

    RSI on the drop bar is about 36.5, under the 40 trigger, so a buy limit is
    posted at round(96 * 0.985, 2) = 94.56 for the next bar.
    """
    prefix = np.array([100.0 if k % 2 == 0 else 101.0 for k in range(30)])
    return np.concatenate([prefix, [96.0]])


def _dip_fill_hold() -> np.ndarray:
    """The next bar closes at 94.0, so its low fills the 94.56 buy limit at 94.56.

    The sell limit is then round(94.56 * 1.03, 2) = 97.40. Flat closes at 94
    never reach it, so the position is held to the last bar.
    """
    return np.concatenate([_dip_prefix(), np.full(10, 94.0)])


def test_history_shorter_than_rsi_warmup_returns_empty():
    closes = np.linspace(100.0, 110.0, 10)
    assert compute_weights({"SPY": _frame(closes)}) == {}


def test_dip_fills_limit_and_holds_at_last_bar():
    weights = compute_weights({"SPY": _frame(_dip_fill_hold())})
    assert weights == {"SPY": pytest.approx(FRACTION)}


def test_limit_never_fills_and_returns_empty():
    # Flat at 96 keeps the low above the 94.56 buy limit, so nothing fills.
    closes = np.concatenate([_dip_prefix(), np.full(10, 96.0)])
    assert compute_weights({"SPY": _frame(closes)}) == {}


def test_sell_limit_fires_and_returns_empty():
    # After the fill at 94.56, a close of 97.5 reaches the 97.40 sell limit.
    closes = np.concatenate([_dip_prefix(), [94.0, 94.0, 97.5], np.full(3, 97.5)])
    assert compute_weights({"SPY": _frame(closes)}) == {}


def test_rising_market_never_dips_and_returns_empty():
    closes = np.linspace(100.0, 150.0, 60)
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
    assert QuantsmdLimitRsiDipBot.param_grid == {
        "buy_rsi": [30.0, 40.0, 50.0],
    }
    assert QuantsmdLimitRsiDipBot.BACKTEST_PERIOD == "max"
    assert BUY_RSI == 40.0
