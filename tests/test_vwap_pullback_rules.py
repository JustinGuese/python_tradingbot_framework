"""Tests for tradingbot/utils/vwap_pullback_rules.py."""

import pandas as pd
import pytest

from tradingbot.utils.vwap_pullback_rules import VWAPPullbackRules

START = pd.Timestamp("2026-01-14 14:30", tz="UTC")  # 09:30 EST


def make_bars(spec, total=78):
    """spec: {bar_index: (high, low, close)}; all other bars sit flat at 100."""
    rows = []
    for i in range(total):
        h, lo, c = spec.get(i, (100.1, 99.9, 100.0) if i < 3 else (100.05, 99.95, 100.0))
        rows.append(
            {
                "timestamp": START + pd.Timedelta(minutes=5 * i),
                "open": c,
                "high": h,
                "low": lo,
                "close": c,
                "volume": 1000.0,
            }
        )
    return pd.DataFrame(rows)


RULES = VWAPPullbackRules(or_minutes=15, swing_bars=3, min_risk_pct=0.0001)


def breakout_then_pullback(extra=None):
    # bars 0-2 are the opening range (high 100.1); bar 3 closes above it,
    # bar 4 pulls back into VWAP and closes back above it -> entry at bar 4.
    spec = {3: (101.0, 100.5, 100.9), 4: (100.9, 100.0, 100.5)}
    spec.update(extra or {})
    return make_bars(spec)


def test_no_breakout_means_no_trade():
    # price chops around VWAP all day but never breaks the opening range
    spec = {i: (100.05, 99.95, 100.0 + (0.02 if i % 2 else -0.02)) for i in range(3, 78)}
    assert RULES.simulate_session(make_bars(spec)).trade is None


def test_breakout_then_pullback_enters_with_exact_levels():
    trade = RULES.simulate_session(breakout_then_pullback()).trade
    assert trade is not None
    assert trade.entry == 100.5
    assert trade.stop == 99.9  # min low of bars 2..4
    assert trade.target == pytest.approx(100.5 + 2.0 * 0.6)
    assert trade.entry_ts == START + pd.Timedelta(minutes=20)


def test_target_exit_is_two_r():
    trade = RULES.simulate_session(breakout_then_pullback({6: (102.0, 100.4, 101.8)})).trade
    assert trade.exit_reason == "target"
    assert trade.r_multiple == pytest.approx(2.0)


def test_stop_exit_is_minus_one_r():
    trade = RULES.simulate_session(breakout_then_pullback({6: (100.5, 99.0, 99.5)})).trade
    assert trade.exit_reason == "stop"
    assert trade.r_multiple == pytest.approx(-1.0)


def test_stop_wins_when_one_bar_touches_both():
    trade = RULES.simulate_session(breakout_then_pullback({6: (103.0, 99.0, 100.0)})).trade
    assert trade.exit_reason == "stop"


def test_flat_before_close_exit():
    trade = RULES.simulate_session(breakout_then_pullback()).trade
    assert trade.exit_reason == "time"
    # forced flat 10 minutes before 16:00 -> last allowed bar starts 15:45 NY (bar 75)
    assert trade.exit_ts == START + pd.Timedelta(minutes=5 * 75)


def test_partial_session_leaves_position_open():
    trade = RULES.simulate_session(breakout_then_pullback().iloc[:10]).trade
    assert trade.exit_reason == "open"


def test_tiny_risk_pullback_is_skipped():
    strict = VWAPPullbackRules(swing_bars=3, min_risk_pct=0.05)
    assert strict.simulate_session(breakout_then_pullback()).trade is None


def test_one_trade_per_day():
    spec = {6: (100.5, 99.0, 99.5), 8: (101.0, 100.0, 100.8)}
    result = RULES.simulate_session(breakout_then_pullback(spec))
    assert result.trade.exit_reason == "stop"
