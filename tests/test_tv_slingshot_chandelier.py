"""Tests for TradingView Slingshot and Chandelier Exit indicators."""

from __future__ import annotations

import os

os.environ.setdefault("POSTGRES_URI", "postgresql://ci:ci@localhost:5432/ci")

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils.tv_slingshot_chandelier import (
    chandelier_exit,
    slingshot,
)


def test_chandelier_exit_rising_run_and_consolidation():
    """long_stop is non-decreasing across a rising run and holds through consolidation.

    Across a rising run with new highs, long_stop ratchets up.
    During a subsequent consolidation where close stays above long_stop,
    the ratcheted long_stop never decreases (holds constant even as rolling peak rolls off).
    """
    n_rising = 25
    n_cons = 15
    total = n_rising + n_cons

    idx = pd.date_range("2023-01-01 00:00", periods=total, freq="1h")

    # Rising phase: close ascends by 2.0 each bar up to 148.0
    rising_close = [100.0 + i * 2.0 for i in range(n_rising)]
    rising_high = [c + 1.0 for c in rising_close]
    rising_low = [c - 1.0 for c in rising_close]

    # Consolidation phase: price consolidates at 146.0 (below peak, above long_stop)
    cons_close = [146.0 for _ in range(n_cons)]
    cons_high = [147.0 for _ in range(n_cons)]
    cons_low = [145.0 for _ in range(n_cons)]

    closes = rising_close + cons_close
    highs = rising_high + cons_high
    lows = rising_low + cons_low

    df = pd.DataFrame(
        {
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": 100.0,
        },
        index=idx,
    )

    period = 10
    mult = 2.0
    ce = chandelier_exit(df, period=period, mult=mult)

    # Warmup bars [0 .. period-2] must be NaN
    assert ce["long_stop"].iloc[: period - 1].isna().all()

    # Once valid, long_stop must be defined
    valid_long = ce["long_stop"].iloc[period - 1 :]
    assert valid_long.notna().all()

    # Across the entire run, long_stop is strictly non-decreasing
    diffs = valid_long.diff().dropna()
    assert (diffs >= -1e-9).all(), "long_stop decreased despite close staying above it"

    # Upward ratcheting occurred during the rising phase
    rising_diffs = ce["long_stop"].iloc[period - 1 : n_rising].diff().dropna()
    assert (rising_diffs > 0).any(), "long_stop should ratchet upwards with rising highs"

    # Holds flat during consolidation once peak lookback rolls off
    tail_stops = ce["long_stop"].iloc[-7:]
    assert np.allclose(tail_stops, tail_stops.iloc[0]), "long_stop should hold through consolidation"

    # Direction stays +1 throughout as close stays above stop
    assert (ce["direction"].iloc[period - 1 :] == 1.0).all()


def test_chandelier_exit_close_below_stop_flips_direction():
    """A close below long_stop flips direction to -1 on that bar and fires sell."""
    idx = pd.date_range("2023-01-01 00:00", periods=20, freq="1h")

    # Bars 0 to 14: steady uptrend from 100 to 128
    closes = [100.0 + i * 2.0 for i in range(15)]
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]

    # Bar 15: sharp crash closing far below long_stop
    closes.append(70.0)
    highs.append(85.0)
    lows.append(69.0)

    # Bars 16 to 19: continued depression
    for i in range(4):
        closes.append(70.0 - i)
        highs.append(71.0 - i)
        lows.append(68.0 - i)

    df = pd.DataFrame(
        {
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": 500.0,
        },
        index=idx,
    )

    ce = chandelier_exit(df, period=5, mult=2.0)

    # At bar 14, direction is still bullish
    assert ce["direction"].iloc[14] == 1.0
    assert not ce["sell"].iloc[14]

    # At bar 15 (crash bar), close is below long_stop
    stop_at_14 = ce["long_stop"].iloc[14]
    assert df["close"].iloc[15] < stop_at_14

    # Direction flips to -1.0 exactly on bar 15, and sell signal fires
    assert ce["direction"].iloc[15] == -1.0
    assert ce["sell"].iloc[15] == True  # noqa: E712  # exact bool compare; truthiness would pass NaN
    assert not ce["buy"].iloc[15]

    # Subsequent bars remain bearish, but sell signal is False (fires only on flip)
    assert (ce["direction"].iloc[16:] == -1.0).all()
    assert not ce["sell"].iloc[16:].any()


def test_slingshot_pullback_and_overextended():
    """A pullback into the channel in an uptrend sets pullback_long.

    A subsequent spike far above it sets overextended and clears pullback_long.
    """
    n = 60
    idx = pd.date_range("2023-01-01 00:00", periods=n, freq="1h")

    # Build an initial uptrend: prices climbing from 100 to 150 over 50 bars
    # This establishes ema_fast > ema_slow (bull_trend = True)
    closes = [100.0 + i * 1.0 for i in range(50)]
    highs = [c + 1.5 for c in closes]
    lows = [c - 1.5 for c in closes]

    # Bar 50: Steady bar at 150
    closes.append(150.0)
    highs.append(151.0)
    lows.append(149.0)

    # Fast=5, Slow=10 channel at bar 50 will be roughly [146, 149]
    # Bar 51: Pullback into the channel
    # low dips into channel (e.g. 147), close stays at or above slow EMA (e.g. 148)
    closes.append(148.0)
    highs.append(151.0)
    lows.append(146.5)

    # Bar 52: Massive green spike far above the channel top
    # Close shoots up past channel_top + max_extension_atr * ATR
    closes.append(220.0)
    highs.append(225.0)
    lows.append(147.0)  # low touches channel, but close is radically overextended

    # Remaining bars to reach 60
    for _ in range(7):
        closes.append(220.0)
        highs.append(222.0)
        lows.append(218.0)

    df = pd.DataFrame(
        {
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": 1000.0,
        },
        index=idx,
    )

    ss = slingshot(df, fast=5, slow=10, max_extension_atr=2.0, atr_period=5)

    # Bar 51 check:
    # bull_trend is True, in_zone is True, not overextended -> pullback_long is True
    assert ss["bull_trend"].iloc[51]
    assert ss["in_zone"].iloc[51]
    assert not ss["overextended"].iloc[51]
    assert ss["pullback_long"].iloc[51]

    # Bar 52 check:
    # Huge spike makes overextended True, which clears pullback_long to False
    assert ss["bull_trend"].iloc[52]
    assert ss["overextended"].iloc[52]
    assert not ss["pullback_long"].iloc[52], "overextended spike should clear pullback_long"


def test_slingshot_chandelier_edge_cases_and_validation():
    """Verify parameter validation, empty frames, and short inputs."""
    # Parameter validation
    df_sample = pd.DataFrame(
        {"close": [10.0, 11.0, 12.0]},
        index=pd.date_range("2023-01-01", periods=3, freq="1D"),
    )

    with pytest.raises(ValueError, match="fast"):
        slingshot(df_sample, fast=0)
    with pytest.raises(ValueError, match="slow"):
        slingshot(df_sample, slow=-1)
    with pytest.raises(ValueError, match="atr_period"):
        slingshot(df_sample, atr_period=0)
    with pytest.raises(ValueError, match="max_extension_atr"):
        slingshot(df_sample, max_extension_atr=-0.5)

    with pytest.raises(ValueError, match="period"):
        chandelier_exit(df_sample, period=0)
    with pytest.raises(ValueError, match="mult"):
        chandelier_exit(df_sample, mult=-1.0)

    # Empty frame
    empty_df = pd.DataFrame()
    ss_empty = slingshot(empty_df)
    assert ss_empty.empty
    assert list(ss_empty.columns) == [
        "ema_fast",
        "ema_slow",
        "bull_trend",
        "bear_trend",
        "in_zone",
        "pullback_long",
        "pullback_short",
        "overextended",
    ]

    ce_empty = chandelier_exit(empty_df)
    assert ce_empty.empty
    assert list(ce_empty.columns) == ["long_stop", "short_stop", "direction", "buy", "sell"]

    # Short frame (fewer rows than period) returns NaNs and False
    ce_short = chandelier_exit(df_sample, period=10)
    assert len(ce_short) == 3
    assert ce_short["long_stop"].isna().all()
    assert ce_short["direction"].isna().all()
    assert not ce_short["buy"].any()
    assert not ce_short["sell"].any()
