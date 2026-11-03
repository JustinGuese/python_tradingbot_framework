import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_intraday_5m_bracket_reversion import (
    FRACTION,
    UNIVERSE,
    QuantsmdIntraday5mBracketReversionBot,
    compute_weights,
)


def _bars(closes, half_range: float = 0.01) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    index = pd.date_range("2026-01-05 14:30", periods=len(closes), freq="5min")
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes + half_range,
            "low": closes - half_range,
            "close": closes,
        },
        index=index,
    )


def _dip_then_flat() -> np.ndarray:
    # 20 flat bars, one 1% drop (RSI goes to 0 and the bot buys at 99.0), then flat.
    return np.concatenate([np.full(20, 100.0), [99.0], np.full(10, 99.0)])


def test_too_few_rows_returns_empty():
    assert compute_weights({"SPY": _bars(np.full(5, 100.0))}) == {}


def test_dip_then_held_at_last_bar():
    # Entry at the 99.0 close; the bracket is 98.60 stop and 99.59 target, and
    # every later bar stays inside it.
    ohlc = {"SPY": _bars(_dip_then_flat())}
    assert compute_weights(ohlc) == pytest.approx({"SPY": FRACTION})


def test_rising_market_never_enters():
    closes = 100.0 + 0.1 * np.arange(60)
    ohlc = {"SPY": _bars(closes)}
    assert compute_weights(ohlc) == {}


def test_stop_hit_on_last_bar_goes_flat():
    closes = _dip_then_flat()
    frame = _bars(closes)
    # Last bar trades down through the 98.60 stop, so the bracket exits on that bar.
    frame.loc[frame.index[-1], "low"] = 98.0
    assert compute_weights({"SPY": frame}) == {}


def test_target_hit_on_last_bar_goes_flat():
    closes = _dip_then_flat()
    frame = _bars(closes)
    # Last bar trades up through the 99.59 target, so the bracket exits on that bar.
    frame.loc[frame.index[-1], "high"] = 100.0
    assert compute_weights({"SPY": frame}) == {}


def test_no_reentry_on_bar_that_closes_position():
    # The stop closes the position on the last bar. That bar must not also buy
    # again, even though RSI is still below 30.
    closes = _dip_then_flat()
    frame = _bars(closes)
    frame.loc[frame.index[-1], "low"] = 98.0
    assert compute_weights({"SPY": frame}) == {}


def test_generic_weights_nonnegative_and_bounded():
    rng = np.random.default_rng(5)
    ohlc = {}
    for t in UNIVERSE:
        steps = rng.normal(0.0, 0.003, 400)
        closes = 100.0 * np.exp(np.cumsum(steps))
        ohlc[t] = _bars(closes, half_range=0.05)
    weights = compute_weights(ohlc)
    assert all(w >= 0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights
    assert set(weights) <= set(UNIVERSE)


def test_generic_bot_class_metadata_readable():
    assert QuantsmdIntraday5mBracketReversionBot.BACKTEST_PERIOD == "60d"
    assert UNIVERSE == ["SPY", "QQQ", "IWM"]
