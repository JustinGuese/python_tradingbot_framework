"""
Unit tests for Form 8-K buyback announcement study (Step B1).

Strictly pure functions: no network, no DB access.
Covers:
- Regex matching & rejection
- Look-ahead guard (intraday, after-close, date-only)
- 30-day per-company deduplication
- Announcement AR filter & market cap partitioning
- Strategy returns with execution costs & cash days
"""

from __future__ import annotations

from datetime import UTC

import pandas as pd
import pytest

from scripts.onetime_event_buyback_study import (
    classify_market_cap_bucket,
    compute_announcement_abnormal_return,
    dedupe_events_by_symbol,
    matches_buyback_regex,
    parse_acceptance_datetime,
    strategy_returns_with_costs,
)
from tradingbot.utils.event_study import _find_entry_bar_idx


def test_buyback_regex_matching_and_rejection():
    """Verify buyback regex detects buyback authorizations and rejects unrelated text."""
    positives = [
        "The Board of Directors authorized a new share repurchase program of up to $10 billion.",
        "Today announced that it approved a stock repurchase authorization of common stock.",
        "The company has authorized the repurchase of up to $5 billion.",
        "Announced a $25 billion share buyback program.",
        "Authorized a new share repurchase facility for up to 50 million shares.",
    ]
    for text in positives:
        assert matches_buyback_regex(text), f"Failed to match positive text: {text}"

    negatives = [
        "The Company announced the appointment of Jane Doe as Chief Financial Officer.",
        "Item 5.07 Submission of Matters to a Vote of Security Holders at annual meeting.",
        "Entered into a second amended and restated revolving credit facility agreement.",
        "Item 2.02 Results of Operations and Financial Condition for Q3.",
    ]
    for text in negatives:
        assert not matches_buyback_regex(text), f"Incorrectly matched negative text: {text}"


def test_look_ahead_guard_intraday_after_close_and_date_only():
    """
    Look-ahead guard:
    - Event before NYSE close (e.g. 14:00 UTC) enters at today's close.
    - Event after NYSE close (e.g. 21:00 UTC) MUST enter at tomorrow's close.
    - Event with only a filing date enters at next session close.
    """
    dates = pd.to_datetime(["2026-03-09", "2026-03-10", "2026-03-11", "2026-03-12"])
    prices = pd.Series([100.0, 105.0, 110.0, 120.0], index=dates)

    # 1. Event before close on Monday 2026-03-09 at 14:00 UTC
    dt_before = parse_acceptance_datetime("2026-03-09T14:00:00.000Z")
    idx_before = _find_entry_bar_idx(prices, dt_before)
    # First close strictly after is Monday close (index 0)
    assert idx_before == 0
    assert prices.index[idx_before] == pd.Timestamp("2026-03-09")

    # 2. Event after close on Monday 2026-03-09 at 21:30 UTC
    dt_after = parse_acceptance_datetime("2026-03-09T21:30:00.000Z")
    idx_after = _find_entry_bar_idx(prices, dt_after)
    # First close strictly after is Tuesday close (index 1)
    assert idx_after == 1
    assert prices.index[idx_after] == pd.Timestamp("2026-03-10")

    # 3. Date-only event: filing date "2026-03-09" without timestamp
    dt_date_only = parse_acceptance_datetime(None, filing_date_str="2026-03-09")
    idx_date_only = _find_entry_bar_idx(prices, dt_date_only)
    # Must enter next session close (Tuesday close, index 1)
    assert idx_date_only == 1
    assert prices.index[idx_date_only] == pd.Timestamp("2026-03-10")


def test_deduplication_within_30_days_per_company():
    """
    Deduplication per company within 30 days:
    - Multiple filings within 30 days keep only the first.
    - Filings >= 30 days apart are preserved.
    - Different companies are kept independently.
    """
    raw_events = pd.DataFrame(
        [
            {"symbol": "AAPL", "acceptance_dt": pd.Timestamp("2024-01-10 15:00:00", tz=UTC)},
            {"symbol": "AAPL", "acceptance_dt": pd.Timestamp("2024-01-20 15:00:00", tz=UTC)},  # +10d: drop
            {"symbol": "MSFT", "acceptance_dt": pd.Timestamp("2024-01-20 15:00:00", tz=UTC)},  # Different sym: keep
            {"symbol": "AAPL", "acceptance_dt": pd.Timestamp("2024-02-15 15:00:00", tz=UTC)},  # +36d: keep
        ]
    )

    deduped = dedupe_events_by_symbol(raw_events, window_days=30)
    assert len(deduped) == 3
    assert list(deduped["symbol"]) == ["AAPL", "MSFT", "AAPL"]
    assert deduped.iloc[0]["acceptance_dt"] == pd.Timestamp("2024-01-10 15:00:00", tz=UTC)
    assert deduped.iloc[2]["acceptance_dt"] == pd.Timestamp("2024-02-15 15:00:00", tz=UTC)


def test_market_cap_bucketing_and_announcement_ar_filter():
    """
    Test size bucketing and announcement abnormal return filter.
    """
    # 1. Market cap median split
    symbols = ["AAPL", "MSFT", "SMALL_A", "SMALL_B"]
    caps = {"AAPL": 3e12, "MSFT": 3e12, "SMALL_A": 5e10, "SMALL_B": 4e10}
    buckets, med = classify_market_cap_bucket(symbols, caps)

    assert med == pytest.approx(1.525e12)
    assert buckets["AAPL"] == "above_median"
    assert buckets["MSFT"] == "above_median"
    assert buckets["SMALL_A"] == "below_median"
    assert buckets["SMALL_B"] == "below_median"

    # 2. Announcement AR calculation
    dates = pd.to_datetime(["2026-03-09", "2026-03-10"])
    stock_up = pd.Series([100.0, 103.0], index=dates)  # +3%
    stock_down = pd.Series([100.0, 100.5], index=dates)  # +0.5%
    qqq = pd.Series([400.0, 404.0], index=dates)  # +1%

    # Beta 1.0: stock_up has AR = 3% - 1% = +2%
    ar_pos = compute_announcement_abnormal_return(stock_up, qqq, entry_idx=1, beta=1.0)
    assert ar_pos == pytest.approx(0.02)

    # Beta 1.0: stock_down has AR = 0.5% - 1% = -0.5%
    ar_neg = compute_announcement_abnormal_return(stock_down, qqq, entry_idx=1, beta=1.0)
    assert ar_neg == pytest.approx(-0.005)


def test_strategy_returns_with_costs_and_cash_days():
    """
    Test strategy returns with per-side execution costs:
    - 2 bp/side deducted on entry and exit sessions.
    - Cash days have flat 0.0 returns.
    """
    dates = pd.date_range("2026-03-09", periods=5, freq="B")
    # Day 0: 100, Day 1: 102 (+2%), Day 2: 104 (+1.96%), Day 3: 105 (+0.96%), Day 4: 106
    prices = {"AAPL": pd.Series([100.0, 102.0, 104.0, 105.0, 106.0], index=dates)}

    # Signal on Day 0 before close, hold 2 sessions (Day 1 and Day 2)
    signals = pd.DataFrame([{"symbol": "AAPL", "acceptance_dt": pd.Timestamp("2026-03-09 14:00:00", tz=UTC)}])

    cost = 0.0002  # 2 bp
    strat_rets = strategy_returns_with_costs(signals, prices, hold_days=2, cost_per_side=cost)

    # 4 return sessions: Day 1, Day 2, Day 3, Day 4
    assert len(strat_rets) == 4
    # Day 1 is entry session: ret = (102 - 100)/100 - cost = 0.02 - 0.0002 = 0.0198
    assert strat_rets.iloc[0] == pytest.approx(0.02 - cost)
    # Day 2 is exit session: ret = (104 - 102)/102 - cost = 2/102 - 0.0002
    assert strat_rets.iloc[1] == pytest.approx((104.0 - 102.0) / 102.0 - cost)
    # Day 3 and Day 4 are cash days -> strictly 0.0
    assert strat_rets.iloc[2] == 0.0
    assert strat_rets.iloc[3] == 0.0
