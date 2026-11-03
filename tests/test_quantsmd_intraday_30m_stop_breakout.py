import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_intraday_30m_stop_breakout import (
    FRACTION,
    HOLD_BARS,
    LOOKBACK,
    UNIVERSE,
    QuantsmdIntraday30mStopBreakoutBot,
    compute_weights,
)


def _bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """Build an open/high/low/close frame from explicit (o, h, l, c) rows, 30 minutes apart."""
    arr = np.asarray(rows, dtype=float)
    index = pd.date_range("2026-01-05 14:30", periods=len(arr), freq="30min")
    return pd.DataFrame(arr, columns=["open", "high", "low", "close"], index=index)


def _flat(n: int, price: float = 100.0) -> list[tuple[float, float, float, float]]:
    return [(price, price, price, price)] * n


def _breakout() -> list[tuple[float, float, float, float]]:
    """A bar whose high clears the 100.1 stop set from the flat highs of 100."""
    return [(100.0, 101.0, 100.0, 101.0)]


def _frame(closes: np.ndarray, spread: float = 0.0) -> pd.DataFrame:
    close = np.asarray(closes, dtype=float)
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    index = pd.date_range("2026-01-05 14:30", periods=len(close), freq="30min")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=index)


def test_history_shorter_than_lookback_returns_empty():
    assert compute_weights({"SPY": _bars(_flat(LOOKBACK))}) == {}


def test_too_few_rows_returns_empty():
    assert compute_weights({"SPY": _bars(_flat(5))}) == {}


def test_breakout_fills_and_holds_at_last_bar():
    # Stop 100.1 is submitted on bar 12 from the highs of bars 0..11. Bar 13 breaks
    # out, and bars 14..16 keep the position inside the 8-bar time stop.
    rows = _flat(13) + _breakout() + _flat(3, 101.0)
    weights = compute_weights({"SPY": _bars(rows)})
    assert set(weights) == {"SPY"}
    assert weights["SPY"] == pytest.approx(FRACTION)


def test_stop_order_expires_unfilled_and_returns_empty():
    # Every high stays at 100, below the 100.1 stop, so no order ever fills.
    assert compute_weights({"SPY": _bars(_flat(40))}) == {}


def test_flat_market_never_breaks_out():
    assert compute_weights({"SPY": _bars(_flat(60))}) == {}


def test_time_stop_fires_on_last_bar_and_returns_empty():
    # Entry bar is 12 (the submission bar), so the time stop fires on 12 + HOLD_BARS = 20.
    rows = _flat(13) + _breakout() + _flat(HOLD_BARS - 1, 101.0) + _flat(1, 101.0)
    assert len(rows) == 13 + 1 + HOLD_BARS
    assert compute_weights({"SPY": _bars(rows)}) == {}


def test_held_one_bar_before_time_stop():
    rows = _flat(13) + _breakout() + _flat(HOLD_BARS - 2, 101.0)
    weights = compute_weights({"SPY": _bars(rows)})
    assert weights == {"SPY": pytest.approx(FRACTION)}


def test_missing_ticker_is_ignored():
    assert compute_weights({}) == {}


def test_random_walk_invariants():
    rng = np.random.default_rng(11)
    ohlc = {}
    for t in UNIVERSE:
        steps = rng.normal(0.0, 0.003, 400)
        closes = 100.0 * np.exp(np.cumsum(steps))
        ohlc[t] = _frame(closes, spread=0.05)
    weights = compute_weights(ohlc)
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert all(w >= 0 for w in weights.values())
    assert "USD" not in weights
    assert set(weights) <= set(UNIVERSE)


def test_bot_class_metadata_readable():
    assert QuantsmdIntraday30mStopBreakoutBot.__name__ == "QuantsmdIntraday30mStopBreakoutBot"
    assert QuantsmdIntraday30mStopBreakoutBot.BACKTEST_PERIOD == "60d"
    assert UNIVERSE == ["SPY", "QQQ", "IWM"]
