"""
Tests for live-vs-paper tracking (tradingbot/utils/live_tracking.py).

Step S1c of PLAN.md:
  * Flat-broker detection
  * Weekday/gap filtering
  * Drift and tracking error on synthetic series
  * In-memory DB integration & edge cases
"""

from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tradingbot.utils.db import Base, LiveEquity, PortfolioWorth
from tradingbot.utils.live_tracking import (
    build_paper_blend_series,
    compute_tracking_metrics,
    format_tracking_table,
    is_broker_flat,
    is_paper_invested,
    joint_returns,
    track_broker,
    weekday_levels,
)


# --------------------------------------------------------------------------- #
# 1. Weekday and Gap Filtering (AGENTS.md rules)
# --------------------------------------------------------------------------- #
def test_weekday_and_gap_filtering():
    # 2026-09-04 is Fri, 05 Sat, 06 Sun, 07 Mon, 08 Tue, 15 next Tue (+7d gap)
    idx = pd.to_datetime(["2026-09-04", "2026-09-05", "2026-09-06", "2026-09-07", "2026-09-08", "2026-09-15"])
    worth = pd.Series([100.0, 100.0, 100.0, 102.0, 104.0, 120.0], index=idx)

    levels = weekday_levels(worth)
    # Weekends (Sat, Sun) dropped; weekdays kept
    assert list(levels.index.dayofweek) == [4, 0, 1, 1]

    # Non-positive valuation drops
    glitch = worth.copy()
    glitch.iloc[3] = 0.0  # Monday valuation failure
    assert len(weekday_levels(glitch)) == 3

    # Joint returns: Fri->Mon (3 days) is kept; Tue->next Tue (7 days) is dropped as outage
    rets = joint_returns(worth, worth)
    assert list(rets.index.date) == [date(2026, 9, 7), date(2026, 9, 8)]


# --------------------------------------------------------------------------- #
# 2. Flat-Broker and Invested Detection
# --------------------------------------------------------------------------- #
def test_flat_broker_detection():
    # Broker flat checks
    assert is_broker_flat(None, live_return=0.0) is True
    assert is_broker_flat(None, live_return=0.01) is False
    assert is_broker_flat({}) is True
    assert is_broker_flat({"USD": 10000.0}) is True
    assert is_broker_flat({"USD": 5000.0, "CASH": 5000.0}) is True
    assert is_broker_flat({"SPY": 0.0, "QQQ": 0.0}) is True
    assert is_broker_flat({"SPY": 1e-7}) is True  # dust
    assert is_broker_flat({"SPY": 10.0}) is False
    assert is_broker_flat({"QQQ": -5.0}) is False  # short position is not flat


def test_paper_invested_detection():
    # Paper invested checks
    assert is_paper_invested(None, paper_return=0.0) is False
    assert is_paper_invested(None, paper_return=0.005) is True
    assert is_paper_invested({}) is False
    assert is_paper_invested({"USD": 10000.0}) is False
    assert is_paper_invested({"USD": 2000.0, "SPY": 15.0}) is True
    assert is_paper_invested({"BTC": 0.5}) is True


# --------------------------------------------------------------------------- #
# 3. Drift and Tracking Error on Synthetic Series
# --------------------------------------------------------------------------- #
def test_drift_and_tracking_perfect_match():
    """When live tracks paper 1:1, drift is 0, TE is 0, beta is 1.0."""
    dates = pd.date_range("2026-10-01", periods=10, freq="B")  # business days
    returns = [0.01, -0.005, 0.02, -0.01, 0.015, -0.02, 0.005, 0.01, -0.005, 0.02]
    all_dates = [dates[0] - timedelta(days=1), *list(dates)]
    levels = [100.0]
    for r in returns:
        levels.append(levels[-1] * (1.0 + r))
    paper_levels = pd.Series(levels, index=all_dates)
    live_levels = paper_levels.copy()

    # Both holding positions on all dates
    positions = pd.Series([{"SPY": 10.0}] * len(live_levels), index=live_levels.index)
    holdings = pd.Series([{"SPY": 10.0}] * len(paper_levels), index=paper_levels.index)

    metrics = compute_tracking_metrics(
        live_series=live_levels,
        paper_series=paper_levels,
        live_positions=positions,
        paper_holdings=holdings,
        broker_name="test_broker",
    )

    assert metrics.n_days == 10
    assert metrics.daily_tracking_error == pytest.approx(0.0, abs=1e-8)
    assert metrics.cumulative_drift == pytest.approx(0.0, abs=1e-8)
    assert metrics.beta == pytest.approx(1.0, abs=1e-6)
    assert metrics.flat_days_while_invested == 0
    assert metrics.invested_days == 10
    assert metrics.flat_share == 0.0
    assert metrics.verdict == "TRACKING"


def test_drift_on_constant_divergence():
    """Live drifts +1% per day above paper (which is flat at 0%)."""
    dates = pd.date_range("2026-10-01", periods=5, freq="B")
    all_dates = [dates[0] - timedelta(days=1), *list(dates)]

    paper = pd.Series([100.0] * 6, index=all_dates)
    live = pd.Series([100.0 * (1.01**i) for i in range(6)], index=all_dates)

    metrics = compute_tracking_metrics(
        live_series=live,
        paper_series=paper,
        broker_name="drifting_broker",
    )

    assert metrics.n_days == 5
    expected_drift = (1.01**5) - 1.0  # +5.101%
    assert metrics.live_cumulative_return == pytest.approx(expected_drift, rel=1e-4)
    assert metrics.paper_cumulative_return == pytest.approx(0.0, abs=1e-6)
    assert metrics.cumulative_drift == pytest.approx(expected_drift, rel=1e-4)
    # Since difference is constant 0.01 every day, standard deviation is 0.0
    assert metrics.daily_tracking_error == pytest.approx(0.0, abs=1e-8)


def test_flat_broker_while_blend_invested():
    """Broker places zero trades (all cash), while blend is actively invested."""
    dates = pd.date_range("2026-10-01", periods=6, freq="B")
    all_dates = [dates[0] - timedelta(days=1), *list(dates)]

    # Broker is stuck at 60,000 cash (like Collective2 in TODOS.md)
    live = pd.Series([60_000.0] * 7, index=all_dates)
    positions = pd.Series([{}] * 7, index=all_dates)

    # Blend is invested in active assets
    paper = pd.Series([10_000.0, 10_100.0, 10_050.0, 10_200.0, 10_150.0, 10_300.0, 10_250.0], index=all_dates)
    holdings = pd.Series([{"SPY": 20.0}] * 7, index=all_dates)

    metrics = compute_tracking_metrics(
        live_series=live,
        paper_series=paper,
        live_positions=positions,
        paper_holdings=holdings,
        broker_name="flat_c2",
    )

    assert metrics.n_days == 6
    assert metrics.live_cumulative_return == pytest.approx(0.0, abs=1e-8)
    assert metrics.beta == pytest.approx(0.0, abs=1e-6)
    assert metrics.invested_days == 6
    assert metrics.flat_days_while_invested == 6
    assert metrics.flat_share == 1.0  # 100% of days broker was flat while blend was invested
    assert metrics.verdict == "FLAT (NO TRADES)"


# --------------------------------------------------------------------------- #
# 4. Blend Series Builder
# --------------------------------------------------------------------------- #
def test_build_paper_blend_series():
    dates = pd.date_range("2026-10-01", periods=4, freq="B")
    all_dates = [dates[0] - timedelta(days=1), *list(dates)]

    # Bot A: +1% each day
    # Bot B: +3% each day
    worth_a = pd.Series([100.0 * (1.01**i) for i in range(5)], index=all_dates)
    worth_b = pd.Series([100.0 * (1.03**i) for i in range(5)], index=all_dates)

    # 50/50 blend -> expected daily return = 0.5 * 0.01 + 0.5 * 0.03 = +2%
    weights = {"BotA": 0.5, "BotB": 0.5}
    blend_worth, _invested = build_paper_blend_series(
        worth_by_bot={"BotA": worth_a, "BotB": worth_b},
        weights=weights,
    )

    assert len(blend_worth) == 5
    daily_rets = blend_worth.pct_change().dropna()
    assert len(daily_rets) == 4
    for r in daily_rets:
        assert r == pytest.approx(0.02, rel=1e-4)


# --------------------------------------------------------------------------- #
# 5. DB Integration & End-to-End
# --------------------------------------------------------------------------- #
def test_track_broker_sqlite_in_memory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    dates = pd.date_range("2026-10-01", periods=6, freq="B")

    # Seed PortfolioWorth for Bot1
    for i, d in enumerate(dates):
        pw = PortfolioWorth(
            bot_name="AdaptiveMeanReversionBot",
            date=d.to_pydatetime(),
            portfolio_worth=10_000.0 * (1.01**i),
            holdings={"QQQ": 10.0},
        )
        session.add(pw)

    # Seed LiveEquity for interactive_brokers
    for i, d in enumerate(dates):
        eq = LiveEquity(
            broker="interactive_brokers",
            account_id="U123456",
            date=d.to_pydatetime(),
            timestamp=d.to_pydatetime(),
            equity=50_000.0 * (1.01**i),
            cash=5_000.0,
            positions={"QQQ": 50.0},
            bot_weights='{"AdaptiveMeanReversionBot": 1.0}',
            is_testnet=False,
        )
        session.add(eq)
    session.commit()

    metrics = track_broker(session, broker="interactive_brokers")
    assert metrics is not None
    assert metrics.broker == "interactive_brokers"
    assert metrics.n_days == 5
    assert metrics.daily_tracking_error == pytest.approx(0.0, abs=1e-6)
    assert metrics.flat_share == 0.0
    assert metrics.verdict == "TRACKING"

    # Test table formatting
    table = format_tracking_table([metrics])
    assert "| interactive_brokers |" in table
    assert "| TRACKING |" in table

    session.close()


# --------------------------------------------------------------------------- #
# 6. Edge Cases & Resilience
# --------------------------------------------------------------------------- #
def test_empty_or_single_observation_does_not_crash():
    empty_series = pd.Series(dtype=float)
    metrics = compute_tracking_metrics(empty_series, empty_series, broker_name="empty")
    assert metrics.n_days == 0
    assert metrics.daily_tracking_error == 0.0
    assert metrics.cumulative_drift == 0.0
    assert metrics.verdict == "NO COMMON DATA"

    # Single observation
    one_date = pd.to_datetime(["2026-10-01"])
    one_series = pd.Series([100.0], index=one_date)
    metrics_one = compute_tracking_metrics(one_series, one_series, broker_name="one")
    assert metrics_one.n_days == 0  # 1 level gives 0 returns

    table = format_tracking_table([])
    assert "No tracking metrics available" in table
