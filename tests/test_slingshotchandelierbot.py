"""Tests for SlingshotChandelierBot (Step S2d)."""

from __future__ import annotations

import os

os.environ.setdefault("POSTGRES_URI", "postgresql://ci:ci@localhost:5432/ci")

import pandas as pd
import pytest

from tradingbot.slingshotchandelierbot import (
    CH_MULT,
    CH_PERIOD,
    FAST,
    SLOW,
    UNIVERSE,
    SlingshotChandelierBot,
    compute_weights,
)
from tradingbot.utils.botclass import Bot


def _make_bull_trend_frame(
    n_flat: int = 30,
    n_rising: int = 20,
    step: float = 3.0,
    base_price: float = 100.0,
) -> pd.DataFrame:
    """Create a synthetic OHLCV frame with an established bull trend.

    Flat base followed by steep rise ensures price is well above the EMA channel
    and no spurious early pullbacks occurred during warmup.
    """
    total = n_flat + n_rising
    idx = pd.date_range("2023-01-01 00:00", periods=total, freq="1h")
    closes = [base_price for _ in range(n_flat)]
    for i in range(1, n_rising + 1):
        closes.append(base_price + i * step)

    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]
    return pd.DataFrame(
        {
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": 1000.0,
        },
        index=idx,
    )


def test_entry_only_on_pullback_in_bull_trend():
    """(1) Entry only on a pullback in a bull trend.

    - Monotonic trend above the channel does not enter.
    - Pullback touching the EMA channel (closing >= slow EMA) triggers entry.
    - Pullback in a bear trend does not trigger entry.
    """
    fast, slow = 5, 10
    ch_period, ch_mult = 5, 2.0

    # Case A: Uptrend running above the channel without pullback
    df_no_pullback = _make_bull_trend_frame()
    w_no_pullback = compute_weights(
        {"SPY": df_no_pullback},
        fast=fast,
        slow=slow,
        ch_period=ch_period,
        ch_mult=ch_mult,
    )
    assert w_no_pullback == {}, "Should not enter without a pullback into the EMA channel"

    # Case B: Uptrend with a pullback bar into the channel
    df_pullback = df_no_pullback.copy()
    idx_next = pd.date_range(df_pullback.index[-1] + pd.Timedelta(hours=1), periods=1, freq="1h")
    # Low dips into channel [~146.7, ~154.0], close stays >= slow EMA (146.7) and >= long_stop (153.0)
    pb_row = pd.DataFrame(
        {
            "open": [155.0],
            "high": [156.0],
            "low": [150.0],
            "close": [153.5],
            "volume": [1000.0],
        },
        index=idx_next,
    )
    df_with_pb = pd.concat([df_pullback, pb_row])
    w_pullback = compute_weights(
        {"SPY": df_with_pb},
        fast=fast,
        slow=slow,
        ch_period=ch_period,
        ch_mult=ch_mult,
    )
    assert "SPY" in w_pullback
    assert pytest.approx(w_pullback["SPY"], 1e-4) == 0.2

    # Case C: Downtrend with pullback into channel (bear trend should never enter)
    n = 50
    idx_bear = pd.date_range("2023-01-01 00:00", periods=n, freq="1h")
    bear_closes = [200.0 - i * 1.0 for i in range(n)]
    bear_highs = [c + 1.0 for c in bear_closes]
    bear_lows = [c - 1.0 for c in bear_closes]
    df_bear = pd.DataFrame(
        {
            "open": bear_closes,
            "high": bear_highs,
            "low": bear_lows,
            "close": bear_closes,
            "volume": 1000.0,
        },
        index=idx_bear,
    )
    w_bear = compute_weights(
        {"SPY": df_bear},
        fast=fast,
        slow=slow,
        ch_period=ch_period,
        ch_mult=ch_mult,
    )
    assert w_bear == {}, "Bear trend should not enter"


def test_exit_on_close_below_chandelier_stop_not_before():
    """(2) Exit on a close below the Chandelier stop, not before.

    - Pullback in bull trend enters.
    - Subsequent bars trading above long_stop remain held.
    - A bar closing below long_stop triggers an immediate exit.
    """
    fast, slow = 5, 10
    ch_period, ch_mult = 5, 2.0

    df_base = _make_bull_trend_frame()
    # Pullback bar at bar 50
    pb_time = df_base.index[-1] + pd.Timedelta(hours=1)
    pb_row = pd.DataFrame(
        {"open": [155.0], "high": [156.0], "low": [150.0], "close": [153.5], "volume": [1000.0]},
        index=[pb_time],
    )
    df_entered = pd.concat([df_base, pb_row])

    # Bar 51: price continues above long_stop (~153.0)
    b51_time = pb_time + pd.Timedelta(hours=1)
    b51_row = pd.DataFrame(
        {"open": [155.0], "high": [159.0], "low": [155.0], "close": [158.0], "volume": [1000.0]},
        index=[b51_time],
    )
    df_hold = pd.concat([df_entered, b51_row])

    w_hold = compute_weights(
        {"SPY": df_hold},
        fast=fast,
        slow=slow,
        ch_period=ch_period,
        ch_mult=ch_mult,
    )
    assert "SPY" in w_hold, "Position should be held while price is above Chandelier stop"
    assert pytest.approx(w_hold["SPY"], 1e-4) == 0.2

    # Bar 52: crash closing below long_stop
    b52_time = b51_time + pd.Timedelta(hours=1)
    b52_row = pd.DataFrame(
        {"open": [145.0], "high": [145.0], "low": [139.0], "close": [140.0], "volume": [1000.0]},
        index=[b52_time],
    )
    df_exit = pd.concat([df_hold, b52_row])

    w_exit = compute_weights(
        {"SPY": df_exit},
        fast=fast,
        slow=slow,
        ch_period=ch_period,
        ch_mult=ch_mult,
    )
    assert w_exit == {}, "Position should exit when close is below Chandelier long_stop"


def test_overextended_spike_does_not_enter():
    """(3) Overextended spike does not enter.

    A bar whose low touches the channel but whose close is > max_extension_atr * ATR
    above the channel top is marked overextended and must not enter.
    """
    fast, slow = 5, 10
    ch_period, ch_mult = 5, 2.0

    df_base = _make_bull_trend_frame()
    # Huge green spike: touches channel at low=150, but close=250 is radically overextended
    spike_time = df_base.index[-1] + pd.Timedelta(hours=1)
    spike_row = pd.DataFrame(
        {"open": [155.0], "high": [255.0], "low": [150.0], "close": [250.0], "volume": [1000.0]},
        index=[spike_time],
    )
    df_spike = pd.concat([df_base, spike_row])

    w_spike = compute_weights(
        {"SPY": df_spike},
        fast=fast,
        slow=slow,
        ch_period=ch_period,
        ch_mult=ch_mult,
    )
    assert w_spike == {}, "Overextended spike must not enter"


def test_too_little_history_and_edge_cases():
    """Warmup guard: fewer rows than warmup (max(slow, ch_period)) returns {}."""
    short_df = _make_bull_trend_frame(n_flat=5, n_rising=5)
    # Default warmup is max(50, 22) = 50
    assert compute_weights({"SPY": short_df}) == {}
    # Empty frames dict
    assert compute_weights({}) == {}
    # Empty frame in dict
    assert compute_weights({"SPY": pd.DataFrame()}) == {}


def test_replay_idempotency_and_stale_window():
    """State replay is pure, idempotent, and does not invent stale positions."""
    fast, slow = 5, 10
    ch_period, ch_mult = 5, 2.0

    df_base = _make_bull_trend_frame()
    pb_time = df_base.index[-1] + pd.Timedelta(hours=1)
    pb_row = pd.DataFrame(
        {"open": [155.0], "high": [156.0], "low": [150.0], "close": [153.5], "volume": [1000.0]},
        index=[pb_time],
    )
    df = pd.concat([df_base, pb_row])

    # Idempotency
    w1 = compute_weights({"SPY": df}, fast=fast, slow=slow, ch_period=ch_period, ch_mult=ch_mult)
    w2 = compute_weights({"SPY": df}, fast=fast, slow=slow, ch_period=ch_period, ch_mult=ch_mult)
    assert w1 == w2

    # A window with no entry signals in it remains flat
    flat_idx = pd.date_range("2023-01-01 00:00", periods=50, freq="1h")
    flat_df = pd.DataFrame(
        {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0},
        index=flat_idx,
    )
    assert compute_weights({"SPY": flat_df}, fast=fast, slow=slow, ch_period=ch_period, ch_mult=ch_mult) == {}


def test_bot_class_structure_and_target_weights(monkeypatch):
    """Test SlingshotChandelierBot instantiation and targetWeights."""
    assert SlingshotChandelierBot.BACKTEST_PERIOD == "730d"
    assert SlingshotChandelierBot.param_grid == {
        "fast": [20, 34],
        "slow": [50, 75],
        "ch_period": [14, 22],
        "ch_mult": [2.0, 3.0],
    }

    captured: dict = {}

    def fake_init(self, name, **kwargs):
        captured["name"] = name
        captured.update(kwargs)
        self.tickers = kwargs.get("tickers", UNIVERSE)
        self.benchmark_tickers = []
        self.interval = kwargs.get("interval", "1h")
        self.period = kwargs.get("period", "60d")
        self.fast = kwargs.get("fast", FAST)
        self.slow = kwargs.get("slow", SLOW)
        self.ch_period = kwargs.get("ch_period", CH_PERIOD)
        self.ch_mult = kwargs.get("ch_mult", CH_MULT)
        self.datas = {}

    monkeypatch.setattr(Bot, "__init__", fake_init)
    bot = SlingshotChandelierBot()
    assert captured["name"] == "SlingshotChandelierBot"
    assert captured["tickers"] == UNIVERSE
    assert captured["interval"] == "1h"
    assert captured["period"] == "60d"
    assert bot.fast == FAST
    assert bot.slow == SLOW
    assert bot.ch_period == CH_PERIOD
    assert bot.ch_mult == CH_MULT

    # Verify targetWeights with empty datas returns {}
    bot.datas = {}
    assert bot.targetWeights({}) == {}

    # Verify targetWeights with too little data returns {}
    bot.datas = {"SPY": _make_bull_trend_frame(n_flat=5, n_rising=5)}
    assert bot.targetWeights({}) == {}

    # Verify targetWeights with valid pullback entry in datas allocates 1/5
    bot.fast = 5
    bot.slow = 10
    bot.ch_period = 5
    bot.ch_mult = 2.0
    df_valid = _make_bull_trend_frame()
    pb_time = df_valid.index[-1] + pd.Timedelta(hours=1)
    pb_row = pd.DataFrame(
        {"open": [155.0], "high": [156.0], "low": [150.0], "close": [153.5], "volume": [1000.0]},
        index=[pb_time],
    )
    bot.datas = {"SPY": pd.concat([df_valid, pb_row])}
    weights = bot.targetWeights({})
    assert "SPY" in weights
    assert pytest.approx(weights["SPY"], 1e-4) == 0.2
