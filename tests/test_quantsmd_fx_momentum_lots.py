import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_fx_momentum_lots import (
    ATR_WINDOW,
    CAP,
    LOOKBACK,
    RISK,
    QuantsmdFxMomentumLotsBot,
    compute_weights,
)
from tradingbot.utils.quantsmd_common import FX4


def _bars(close: np.ndarray, width: float) -> pd.DataFrame:
    """OHLC bars whose true range is ``width`` on every bar (a flat, narrow market)."""
    index = pd.bdate_range("2020-01-01", periods=len(close))
    return pd.DataFrame(
        {
            "open": close,
            "high": close + width / 2.0,
            "low": close - width / 2.0,
            "close": close,
        },
        index=index,
    )


def _rising(n: int, step: float = 0.001, start: float = 100.0) -> np.ndarray:
    return start + np.arange(n) * step


def _falling(n: int, step: float = 0.001, start: float = 100.0) -> np.ndarray:
    return start - np.arange(n) * step


def test_tiny_atr_hits_cap():
    # ATR is 0.01, so 0.005 * 100 / 0.01 = 50, capped at 0.20.
    n = LOOKBACK + 10
    ohlc = {"EURUSD=X": _bars(_rising(n), width=0.01)}
    weights = compute_weights(ohlc)
    assert weights == {"EURUSD=X": pytest.approx(CAP)}


def test_large_atr_gives_risk_sized_weight():
    # ATR is 10, so the weight is 0.005 * close / 10, which is below the cap.
    n = LOOKBACK + 10
    close = _rising(n)
    ohlc = {"EURUSD=X": _bars(close, width=10.0)}
    weights = compute_weights(ohlc)
    expected = RISK * float(close[-1]) / 10.0
    assert expected < CAP
    assert weights["EURUSD=X"] == pytest.approx(expected, rel=1e-9)


def test_falling_pair_is_omitted_and_sum_below_one():
    n = LOOKBACK + 10
    ohlc = {t: _bars(_rising(n), width=10.0) for t in FX4[:3]}
    ohlc[FX4[3]] = _bars(_falling(n), width=10.0)
    weights = compute_weights(ohlc)
    assert set(weights) == set(FX4[:3])
    assert FX4[3] not in weights
    assert sum(weights.values()) < 1.0


def test_fewer_than_lookback_plus_one_rows_returns_empty():
    # E2: a short history never produces a position.
    ohlc = {t: _bars(_rising(LOOKBACK), width=0.01) for t in FX4}
    assert compute_weights(ohlc) == {}


def test_falling_trend_never_holds():
    # E2 analogue: a downtrend gives no long weight at any size.
    n = LOOKBACK + 10
    ohlc = {t: _bars(_falling(n), width=0.01) for t in FX4}
    assert compute_weights(ohlc) == {}


def test_uptrend_stays_held_on_last_bar():
    # E1: the trend is up over the lookback, so the bot is held at the last bar.
    n = LOOKBACK + 10
    ohlc = {"GBPUSD=X": _bars(_rising(n), width=0.01)}
    weights = compute_weights(ohlc)
    assert "GBPUSD=X" in weights
    assert weights["GBPUSD=X"] > 0


def test_generic_weights_nonnegative_and_bounded():
    rng = np.random.default_rng(11)
    n = 600
    ohlc = {}
    for t in FX4:
        close = 1.0 + np.cumsum(rng.normal(0.0, 0.003, n))
        close = np.abs(close) + 0.5
        ohlc[t] = _bars(close, width=0.01)
    weights = compute_weights(ohlc)
    assert all(w >= 0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights
    assert set(weights) <= set(FX4)


def test_generic_bot_class_metadata_readable():
    assert QuantsmdFxMomentumLotsBot.__name__ == "QuantsmdFxMomentumLotsBot"
    assert QuantsmdFxMomentumLotsBot.param_grid == {"lookback": [63, 126, 252]}
    assert QuantsmdFxMomentumLotsBot.BACKTEST_PERIOD == "max"
    assert LOOKBACK == 126
    assert ATR_WINDOW == 20
