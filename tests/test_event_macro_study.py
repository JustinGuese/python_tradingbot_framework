"""
Unit tests for macro calendar event study (scripts/onetime_event_macro_study.py).

Pure tests: strictly no DB, no network access.
Acceptance:
- Only past events are used (future-dated rows are strictly excluded).
- Window arithmetic uses sessions, not calendar days (handles weekends and holidays).
"""

from datetime import date

import pandas as pd

from scripts.onetime_event_macro_study import (
    build_strategy_signals,
    filter_past_events,
    get_event_sessions,
)


def test_only_past_events_are_used_future_dated_rows_excluded():
    """
    Acceptance: Only past events are used; future-dated rows are excluded.
    """
    raw_events = pd.DataFrame(
        [
            {"kind": "FOMC", "event_date": date(2024, 1, 31)},
            {"kind": "FOMC", "event_date": date(2025, 12, 10)},
            {"kind": "FOMC", "event_date": date(2026, 6, 17)},
            {"kind": "FOMC", "event_date": date(2026, 11, 4)},
            {"kind": "FOMC", "event_date": date(2026, 12, 9)},  # Future
            {"kind": "FOMC", "event_date": date(2027, 1, 27)},  # Future
        ]
    )

    as_of = date(2026, 11, 5)
    filtered = filter_past_events(raw_events, as_of=as_of)

    assert len(filtered) == 4
    assert set(filtered["event_date"]) == {
        date(2024, 1, 31),
        date(2025, 12, 10),
        date(2026, 6, 17),
        date(2026, 11, 4),
    }
    assert date(2026, 12, 9) not in set(filtered["event_date"])
    assert date(2027, 1, 27) not in set(filtered["event_date"])

    # Edge case: all future events -> returns empty DataFrame
    future_only = pd.DataFrame(
        [
            {"kind": "FOMC", "event_date": date(2027, 3, 17)},
            {"kind": "FOMC", "event_date": date(2027, 4, 28)},
        ]
    )
    empty_res = filter_past_events(future_only, as_of=as_of)
    assert empty_res.empty


def test_window_arithmetic_uses_sessions_not_calendar_days_weekend():
    """
    Acceptance: Window arithmetic uses exchange trading sessions, not calendar days.
    Over weekends:
    - Monday event T0: T-1 is Friday (not Sunday or Saturday).
    - Friday event T0: T+1 is Monday (not Saturday or Sunday).
    """
    # 2026-03-09 is Monday
    mon_event = date(2026, 3, 9)
    mon_sessions = get_event_sessions(mon_event)

    assert mon_sessions["T0"] == date(2026, 3, 9)
    assert mon_sessions["T-1"] == date(2026, 3, 6)  # Friday
    assert mon_sessions["T+1"] == date(2026, 3, 10)  # Tuesday
    assert mon_sessions["T+2"] == date(2026, 3, 11)  # Wednesday

    # 2026-03-13 is Friday
    fri_event = date(2026, 3, 13)
    fri_sessions = get_event_sessions(fri_event)

    assert fri_sessions["T0"] == date(2026, 3, 13)
    assert fri_sessions["T-1"] == date(2026, 3, 12)  # Thursday
    assert fri_sessions["T+1"] == date(2026, 3, 16)  # Monday
    assert fri_sessions["T+2"] == date(2026, 3, 17)  # Tuesday


def test_window_arithmetic_uses_sessions_not_calendar_days_holiday():
    """
    Acceptance: Window arithmetic uses sessions, properly skipping NYSE market holidays.
    - Martin Luther King Jr. Day (Monday 2026-01-19) is a market holiday.
    - For an event on Tuesday 2026-01-20: T-1 MUST be Friday 2026-01-16 (skipping Mon holiday).
    - If an event is scheduled on a holiday (e.g. 2026-01-19): T0 rolls forward to Tuesday 2026-01-20.
    """
    tue_after_mlk = date(2026, 1, 20)
    sessions = get_event_sessions(tue_after_mlk)

    assert sessions["T0"] == date(2026, 1, 20)
    assert sessions["T-1"] == date(2026, 1, 16)  # Friday before holiday weekend!

    # Event date landing on the holiday itself
    holiday_event = date(2026, 1, 19)
    h_sessions = get_event_sessions(holiday_event)

    assert h_sessions["T0"] == date(2026, 1, 20)  # Rolled to next session
    assert h_sessions["T-1"] == date(2026, 1, 16)  # Friday before holiday weekend


def test_build_strategy_signals_and_holding_periods():
    """
    Verify strategy signals for [-1,0], [0,+1], and [0,+2] windows.
    - [-1,0]: Entry at T-1 close, hold_days=1.
    - [0,+1]: Entry at T0 close, hold_days=1.
    - [0,+2]: Entry at T0 close, hold_days=2.
    """
    events = [date(2026, 3, 9)]  # Monday
    sig_neg1_0, hold_1 = build_strategy_signals(events, "SPY", "[-1,0]")
    assert hold_1 == 1
    assert len(sig_neg1_0) == 1
    # Entry session must be Friday 2026-03-06
    assert sig_neg1_0.iloc[0]["ts"].date() == date(2026, 3, 6)

    sig_0_1, hold_2 = build_strategy_signals(events, "SPY", "[0,+1]")
    assert hold_2 == 1
    assert len(sig_0_1) == 1
    assert sig_0_1.iloc[0]["ts"].date() == date(2026, 3, 9)

    sig_0_2, hold_3 = build_strategy_signals(events, "SPY", "[0,+2]")
    assert hold_3 == 2
    assert len(sig_0_2) == 1
    assert sig_0_2.iloc[0]["ts"].date() == date(2026, 3, 9)
