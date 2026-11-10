"""
Unit tests for scheduled calendar anomaly legs (scripts/onetime_event_calendar_study.py).

Pure functions only: strictly no DB and no network access.
Tests cover:
  1. EIA Wednesday date generation and holiday shift to Thursday
  2. Turn-of-month (TOM) window math (last session of month to 3rd session of next month)
  3. Pre-holiday session detection (market holidays vs normal weekends)
  4. Quarter-end week windowing (last 5 sessions of calendar quarters)
  5. Transaction cost deductions per round-trip and look-ahead guard
"""

from datetime import date

import pandas as pd
import pytest

from scripts.onetime_event_calendar_study import (
    compute_leg_returns,
    get_eia_dates,
    get_pre_holiday_signals,
    get_quarter_end_week_signals,
    get_turn_of_month_signals,
)


def test_eia_crude_wednesdays_and_holiday_shift_to_thursday():
    """
    Acceptance: EIA dates are every Wednesday, shifting to Thursday if Wednesday
    is an NYSE exchange holiday.
    - 2024-06-19 (Juneteenth) is an NYSE holiday -> shifts to Thursday 2024-06-20.
    - 2024-12-25 (Christmas) is an NYSE holiday -> shifts to Thursday 2024-12-26.
    - 2024-06-12 (normal week) -> Wednesday 2024-06-12.
    """
    eia_june = get_eia_dates(date(2024, 6, 1), date(2024, 6, 30))
    # June 2024 Wednesdays: June 5, June 12, June 19 (holiday), June 26
    assert date(2024, 6, 5) in eia_june
    assert date(2024, 6, 12) in eia_june
    assert date(2024, 6, 19) not in eia_june  # Juneteenth holiday
    assert date(2024, 6, 20) in eia_june  # Shifted to Thursday
    assert date(2024, 6, 26) in eia_june

    eia_dec = get_eia_dates(date(2024, 12, 1), date(2024, 12, 31))
    assert date(2024, 12, 25) not in eia_dec  # Christmas holiday
    assert date(2024, 12, 26) in eia_dec  # Shifted to Thursday


def test_turn_of_month_signals_and_window_math():
    """
    Acceptance: Turn-of-month enters at the close of the last trading session of month M,
    and holds through the first 3 trading sessions of month M+1.
    """
    # Sample trading sessions covering late Jan 2024 to early Feb 2024
    # Jan 2024 last session: 2024-01-31 (Wed)
    # Feb 2024 first 3 sessions: 2024-02-01 (Thu), 2024-02-02 (Fri), 2024-02-05 (Mon)
    jan_feb_sessions = [
        date(2024, 1, 29),
        date(2024, 1, 30),
        date(2024, 1, 31),
        date(2024, 2, 1),
        date(2024, 2, 2),
        date(2024, 2, 5),
        date(2024, 2, 6),
    ]

    tom_signals = get_turn_of_month_signals(jan_feb_sessions)
    assert len(tom_signals) == 1

    entry_s, held_3 = tom_signals[0]
    assert entry_s == date(2024, 1, 31)
    assert held_3 == [date(2024, 2, 1), date(2024, 2, 2), date(2024, 2, 5)]
    # Verify zero lookahead: entry strictly precedes all held sessions
    assert entry_s < held_3[0]


def test_pre_holiday_signals_detects_market_holidays_only():
    """
    Acceptance: Pre-holiday signals detect trading sessions immediately prior
    to exchange market holidays (skipping normal weekend gaps).
    - 2023-11-22 (Wednesday before Thanksgiving Thursday 2023-11-23) is a pre-holiday session.
    - 2023-04-06 (Thursday before Good Friday 2023-04-07) is a pre-holiday session.
    - 2023-11-17 (regular Friday with no holiday following) is NOT a pre-holiday session.
    """
    sample_sessions = [
        date(2023, 4, 5),  # Wed
        date(2023, 4, 6),  # Thu before Good Friday (holiday)
        date(2023, 4, 10),  # Mon
        date(2023, 11, 17),  # Normal Friday
        date(2023, 11, 20),  # Mon
        date(2023, 11, 21),  # Tue
        date(2023, 11, 22),  # Wed before Thanksgiving (holiday)
        date(2023, 11, 24),  # Fri (Black Friday)
    ]

    signals = get_pre_holiday_signals(sample_sessions)
    held_pre_holiday = [held for _, held in signals]

    # Pre-holiday sessions held:
    assert date(2023, 4, 6) in held_pre_holiday  # Thursday before Good Friday
    assert date(2023, 11, 22) in held_pre_holiday  # Wednesday before Thanksgiving
    assert date(2023, 11, 17) not in held_pre_holiday  # Normal Friday


def test_quarter_end_week_signals_last_5_sessions():
    """
    Acceptance: Quarter-end week identifies the last 5 trading sessions of
    months 3, 6, 9, 12, entering at the close of the session immediately preceding them.
    """
    # Sample sessions for Q1 2024 (March end)
    # March 2024 last 5 sessions: Mar 22, 25, 26, 27, 28 (Good Friday March 29 was a holiday)
    march_sessions = [
        date(2024, 3, 20),
        date(2024, 3, 21),
        date(2024, 3, 22),
        date(2024, 3, 25),
        date(2024, 3, 26),
        date(2024, 3, 27),
        date(2024, 3, 28),
    ]

    qe_signals = get_quarter_end_week_signals(march_sessions)
    assert len(qe_signals) == 1

    entry_s, held_5 = qe_signals[0]
    assert len(held_5) == 5
    assert held_5 == [
        date(2024, 3, 22),
        date(2024, 3, 25),
        date(2024, 3, 26),
        date(2024, 3, 27),
        date(2024, 3, 28),
    ]
    # Entry session is immediately prior to held_5[0]
    assert entry_s == date(2024, 3, 21)


def test_transaction_cost_deduction_and_scoring_logic():
    """
    Acceptance: compute_leg_returns correctly applies cost_per_side to entry and exit bars
    (2 * cost_per_side total per trade), while cost_per_side = 0 preserves gross returns.
    """
    # Synthetic prices for 4 consecutive sessions
    dates = pd.date_range("2024-01-02", periods=4, freq="B")
    prices = pd.Series([100.0, 102.0, 104.0, 106.0], index=dates)
    price_dict = {"SPY": prices}

    # Trade: enter on Day 0 close (2024-01-02), hold 2 sessions (Day 1: 2024-01-03 and Day 2: 2024-01-04)
    entry_dates = [dates[0].date()]
    hold_days = 2

    # 1. Gross returns (cost = 0)
    gross_rets = compute_leg_returns("SPY", entry_dates, hold_days, price_dict, cost_per_side=0.0)
    # Day 1 return: (102 - 100) / 100 = 0.02
    assert pytest.approx(gross_rets.loc[dates[1]], abs=1e-6) == 0.02
    # Day 2 return: (104 - 102) / 102 = 0.0196078
    assert pytest.approx(gross_rets.loc[dates[2]], abs=1e-6) == (104.0 - 102.0) / 102.0
    # Day 3 return (not held): 0.0
    assert pytest.approx(gross_rets.loc[dates[3]], abs=1e-6) == 0.0

    # 2. Net returns with 2 bp/side cost (0.0002)
    net_rets = compute_leg_returns("SPY", entry_dates, hold_days, price_dict, cost_per_side=0.0002)
    # Day 1 (entry bar) pays entry cost 0.0002: 0.02 - 0.0002 = 0.0198
    assert pytest.approx(net_rets.loc[dates[1]], abs=1e-6) == 0.02 - 0.0002
    # Day 2 (exit bar) pays exit cost 0.0002: 0.0196078 - 0.0002
    assert pytest.approx(net_rets.loc[dates[2]], abs=1e-6) == (104.0 - 102.0) / 102.0 - 0.0002
    # Total cost deducted across round-trip is exactly 2 * 0.0002 = 0.0004
    total_cost_diff = gross_rets.sum() - net_rets.sum()
    assert pytest.approx(total_cost_diff, abs=1e-6) == 0.0004
