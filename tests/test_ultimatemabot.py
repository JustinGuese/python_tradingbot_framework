"""Tests for TradingView Ultimate Moving Average Bot (CM_Ultimate_MA_MTF)."""

from __future__ import annotations

import os

os.environ.setdefault("POSTGRES_URI", "postgresql://ci:ci@localhost:5432/ci")

import numpy as np
import pandas as pd
import pytest

from tradingbot.ultimatemabot import (
    DEFAULT_HTF,
    DEFAULT_KIND,
    DEFAULT_LENGTH,
    DEFAULT_SLOPE_BARS,
    UNIVERSE,
    UltimateMABot,
    compute_weights,
)
from tradingbot.utils.botclass import Bot


def _make_ohlcv(closes: pd.Series | np.ndarray, index: pd.DatetimeIndex) -> pd.DataFrame:
    """Helper to create standard OHLCV DataFrame."""
    c = pd.Series(closes, index=index, dtype=float)
    return pd.DataFrame(
        {
            "open": c,
            "high": c + 1.0,
            "low": c - 1.0,
            "close": c,
            "volume": 1000.0,
        },
        index=index,
    )


def test_bad_kind_fails_at_construction_time():
    """A bad kind fails at construction time, not at the first trade."""
    with pytest.raises(ValueError, match="Unknown moving average kind"):
        UltimateMABot(kind="non_existent_kind")

    with pytest.raises(ValueError, match="Unknown moving average kind"):
        compute_weights({}, kind="invalid_kind")

    with pytest.raises(ValueError, match="length must be positive"):
        UltimateMABot(length=0)

    with pytest.raises(ValueError, match="period length must be at least 2 for hma"):
        UltimateMABot(kind="hma", length=1)

    with pytest.raises(ValueError, match="slope_bars must be positive"):
        UltimateMABot(slope_bars=0)


def test_falling_htf_ma_forces_flat():
    """Falling HTF MA forces flat even when base MA is rising."""
    # 160 hourly bars: 155 bars falling, followed by 5 bars bouncing up sharply.
    # Base 1h MA turns up (+1.0), but 4h HTF MA remains downward sloping (-1.0).
    idx = pd.date_range("2023-01-01 00:00:00", periods=160, freq="1h")
    vals = np.concatenate([np.linspace(200.0, 100.0, 155), np.linspace(105.0, 140.0, 5)])
    df = _make_ohlcv(vals, idx)

    frames = {"QQQ": df}
    weights = compute_weights(
        frames,
        kind="hma",
        length=20,
        slope_bars=1,
        htf="4h",
    )
    # Falling HTF MA prevents long position, forcing flat (cash)
    assert weights == {}


def test_rising_on_both_goes_long():
    """Rising on both base bar and HTF MA goes long with 1/5 allocation."""
    # 160 hourly bars of monotonic uptrend
    idx = pd.date_range("2023-01-01 00:00:00", periods=160, freq="1h")
    uptrend_qqq = np.linspace(100.0, 200.0, len(idx))
    uptrend_spy = np.linspace(200.0, 300.0, len(idx))
    downtrend_iwm = np.linspace(200.0, 100.0, len(idx))

    frames = {
        "QQQ": _make_ohlcv(uptrend_qqq, idx),
        "SPY": _make_ohlcv(uptrend_spy, idx),
        "IWM": _make_ohlcv(downtrend_iwm, idx),
    }

    weights = compute_weights(
        frames,
        kind="hma",
        length=20,
        slope_bars=1,
        htf="4h",
    )

    # QQQ and SPY are rising on both base and HTF; IWM is falling.
    # Each active ticker in UNIVERSE (size 5) gets 1/5 = 0.20
    assert "QQQ" in weights
    assert "SPY" in weights
    assert "IWM" not in weights
    assert weights["QQQ"] == pytest.approx(0.20)
    assert weights["SPY"] == pytest.approx(0.20)
    assert sum(weights.values()) == pytest.approx(0.40)


def test_fewer_than_warmup_rows_returns_empty():
    """Fewer rows than the warmup period returns empty weights dict."""
    # Empty frames dict
    assert compute_weights({}) == {}

    # 10 hourly bars (well below the warmup requirement)
    idx_short = pd.date_range("2023-01-01 00:00:00", periods=10, freq="1h")
    df_short = _make_ohlcv(np.linspace(100.0, 120.0, len(idx_short)), idx_short)

    assert compute_weights({"QQQ": df_short}) == {}


def test_bot_metadata_and_target_weights(monkeypatch):
    """Verify bot class attributes, initialization parameters, and targetWeights call."""
    assert UltimateMABot.BACKTEST_PERIOD == "730d"
    assert "kind" in UltimateMABot.param_grid
    assert "length" in UltimateMABot.param_grid
    assert "slope_bars" in UltimateMABot.param_grid
    assert "htf" in UltimateMABot.param_grid

    captured: dict = {}

    def fake_init(self, name, **kwargs):
        captured["name"] = name
        captured.update(kwargs)
        self.tickers = kwargs.get("tickers", list(UNIVERSE))
        self.datas = {}

    monkeypatch.setattr(Bot, "__init__", fake_init)

    bot = UltimateMABot()
    assert captured["name"] == "UltimateMABot"
    assert captured["tickers"] == list(UNIVERSE)
    assert captured["interval"] == "1h"
    assert captured["period"] == "60d"
    assert bot.kind == DEFAULT_KIND
    assert bot.length == DEFAULT_LENGTH
    assert bot.slope_bars == DEFAULT_SLOPE_BARS
    assert bot.htf == DEFAULT_HTF

    # Wire up datas on the bot and test targetWeights
    idx = pd.date_range("2023-01-01 00:00:00", periods=160, freq="1h")
    bot.datas = {"QQQ": _make_ohlcv(np.linspace(100.0, 200.0, len(idx)), idx)}
    weights = bot.targetWeights({})
    assert weights == {"QQQ": pytest.approx(0.20)}
