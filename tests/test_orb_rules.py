"""Tests for tradingbot/utils/orb_rules.py."""

import pandas as pd
import pytest

from tradingbot.utils.orb_rules import ORBRules

START = pd.Timestamp("2026-01-14 14:30", tz="UTC")  # 09:30 EST


def make_bars(spec, total=78):
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


# opening range = bars 0-2: high 100.1, low 99.9
RULES = ORBRules(or_minutes=15, window_minutes=90, target_r=0.5, max_stop_pct=0.01)


def test_break_inside_window_enters_with_or_low_stop():
    trade = RULES.simulate_session(make_bars({4: (100.6, 100.0, 100.5)})).trade
    assert trade is not None
    assert trade.entry == 100.5
    assert trade.stop == 99.9
    assert trade.target == pytest.approx(100.5 + 0.5 * 0.6)


def test_break_after_window_is_ignored():
    # bar 18 starts 90 minutes after the open
    assert RULES.simulate_session(make_bars({18: (101.0, 100.0, 100.9)})).trade is None


def test_target_exit():
    trade = RULES.simulate_session(make_bars({4: (100.6, 100.0, 100.5), 5: (101.0, 100.4, 100.9)})).trade
    assert trade.exit_reason == "target"
    assert trade.r_multiple == pytest.approx(0.5)


def test_stop_exit():
    trade = RULES.simulate_session(make_bars({4: (100.6, 100.0, 100.5), 5: (100.5, 99.5, 99.8)})).trade
    assert trade.exit_reason == "stop"
    assert trade.r_multiple == pytest.approx(-1.0)


def test_stop_wins_ties():
    trade = RULES.simulate_session(make_bars({4: (100.6, 100.0, 100.5), 5: (105.0, 99.0, 100.0)})).trade
    assert trade.exit_reason == "stop"


def test_window_end_exit():
    trade = RULES.simulate_session(make_bars({4: (100.6, 100.0, 100.5)})).trade
    assert trade.exit_reason == "time"
    assert trade.exit_ts == START + pd.Timedelta(minutes=85)  # last bar inside the window


def test_max_stop_cap_applied():
    capped = ORBRules(or_minutes=15, target_r=0.5, max_stop_pct=0.001)
    trade = capped.simulate_session(make_bars({4: (100.6, 100.0, 100.5)})).trade
    assert trade.stop == pytest.approx(100.5 * 0.999)


def test_partial_session_stays_open():
    trade = RULES.simulate_session(make_bars({4: (100.6, 100.0, 100.5)}).iloc[:8]).trade
    assert trade.exit_reason == "open"


def test_no_break_no_trade():
    assert RULES.simulate_session(make_bars({})).trade is None
