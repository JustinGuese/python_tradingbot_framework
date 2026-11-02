"""scripts/onetime_intraday_orb_vwap_backtest.py on synthetic sessions."""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils.orb_rules import ORBRules

_spec = importlib.util.spec_from_file_location(
    "intraday_bt", Path(__file__).resolve().parents[1] / "scripts" / "onetime_intraday_orb_vwap_backtest.py"
)
bt = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bt
_spec.loader.exec_module(bt)


def day_bars(day: str, breakout: bool, winner: bool = True) -> pd.DataFrame:
    start = pd.Timestamp(f"{day} 14:30", tz="UTC")  # EST
    rows = []
    for i in range(78):
        h, lo, c = (100.1, 99.9, 100.0) if i < 3 else (100.05, 99.95, 100.0)
        if breakout and i == 4:
            h, lo, c = 100.6, 100.0, 100.5
        if breakout and i == 5:
            h, lo, c = (101.5, 100.4, 101.2) if winner else (100.5, 99.0, 99.5)
        rows.append(
            {
                "timestamp": start + pd.Timedelta(minutes=5 * i),
                "open": c,
                "high": h,
                "low": lo,
                "close": c,
                "volume": 1.0,
            }
        )
    return pd.DataFrame(rows)


@pytest.fixture
def sessions():
    days = [
        ("2026-01-12", True, True),
        ("2026-01-13", False, True),
        ("2026-01-14", True, False),
        ("2026-01-15", True, True),
        ("2026-01-16", False, True),
    ]
    return bt.split_sessions(pd.concat([day_bars(d, b, w) for d, b, w in days], ignore_index=True))


RULES = ORBRules(or_minutes=15, window_minutes=90, target_r=0.5, max_stop_pct=0.01)


def test_split_sessions_one_frame_per_day(sessions):
    assert len(sessions) == 5 and all(len(s) == 78 for s in sessions.values())


def test_cost_application_is_exact():
    trade = RULES.simulate_session(day_bars("2026-01-12", True)).trade
    assert bt.trade_return(trade, 0.0) == pytest.approx(trade.exit / trade.entry - 1)
    s = 0.0005
    assert bt.trade_return(trade, s) == pytest.approx(trade.exit * (1 - s) / (trade.entry * (1 + s)) - 1)
    assert bt.trade_return(trade, s) < bt.trade_return(trade, 0.0)


def test_run_and_summarize(sessions):
    trades = bt.run_rules(sessions, RULES)
    assert sum(t is not None for t in trades.values()) == 3
    bench = bt.qqq_daily_returns(sessions)
    free = bt.summarize("free", trades, bench, 0.0)
    costly = bt.summarize("costly", trades, bench, 0.0005)
    assert free.trades == 3 and free.win_rate == pytest.approx(2 / 3)
    assert free.expectancy_r == pytest.approx((0.5 + -1.0 + 0.5) / 3)
    assert costly.total_return < free.total_return


def test_random_control_is_a_fraction(sessions):
    trades = bt.run_rules(sessions, RULES)
    share = bt.random_control(sessions, trades, 0.5, 90, draws=20)
    assert 0.0 <= share <= 1.0


def test_report_runs(sessions):
    lines = bt.report("t", sessions, RULES, 0.5, 90, 5)
    assert any("random-entry control" in line for line in lines) and np.all(["maxDD" in x for x in lines[1:-1]])
