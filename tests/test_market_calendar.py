"""NYSE sessions, holidays and early closes (utils/market_calendar.py)."""

from datetime import UTC, date, datetime

from tradingbot.option_earningscrushbot import in_entry_window
from tradingbot.utils import market_calendar as mc
from tradingbot.utils import option_rules as rl


def test_holidays_and_early_closes_2026():
    assert not mc.is_session(date(2026, 12, 25))  # Christmas
    assert not mc.is_session(date(2026, 11, 26))  # Thanksgiving
    assert not mc.is_session(date(2026, 7, 3))  # Independence Day observed (the 4th is a Saturday)
    assert mc.is_session(date(2026, 11, 27)) and mc.is_early_close(date(2026, 11, 27))
    assert mc.is_session(date(2026, 12, 24)) and mc.is_early_close(date(2026, 12, 24))
    assert not mc.is_early_close(date(2026, 9, 28))
    # 13:00 New York: 18:00 UTC in winter; a normal close is 20:00 UTC in summer.
    assert mc.session_close_utc(date(2026, 11, 27)) == datetime(2026, 11, 27, 18, 0, tzinfo=UTC)
    assert mc.session_close_utc(date(2026, 9, 28)) == datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
    assert mc.session_close_utc(date(2026, 12, 25)) is None


def test_session_counts_skip_holidays():
    # Mon 2026-11-23 .. Mon 2026-11-30: 5 weekdays, but Thanksgiving is out.
    assert rl.weekdays(date(2026, 11, 23), date(2026, 11, 30)) == 5
    assert rl.business_days(date(2026, 11, 23), date(2026, 11, 30)) == 4
    assert rl.business_days(date(2026, 11, 30), date(2026, 11, 23)) == -4
    assert mc.next_session(date(2026, 11, 25)) == date(2026, 11, 27)
    assert mc.next_session(date(2026, 11, 28)) == date(2026, 11, 30)  # from a Saturday


def test_report_before_a_holiday_reacts_after_it():
    assert rl.reaction_session(date(2026, 11, 25), True) == date(2026, 11, 27)  # skips Thanksgiving
    assert rl.reaction_session(date(2026, 11, 25), False) == date(2026, 11, 25)
    assert rl.reaction_session(date(2026, 11, 25), None) is None


def test_earnings_crush_entry_window_follows_the_close():
    # Winter: a regular close is 21:00 UTC (Nov 20), an early one 18:00 UTC (Nov 27).
    assert in_entry_window(datetime(2026, 11, 20, 19, 30, tzinfo=UTC))
    assert not in_entry_window(datetime(2026, 11, 20, 16, 30, tzinfo=UTC))
    assert not in_entry_window(datetime(2026, 11, 20, 14, 30, tzinfo=UTC))
    assert in_entry_window(datetime(2026, 11, 27, 16, 30, tzinfo=UTC))  # early close: 16:30 enters
    assert not in_entry_window(datetime(2026, 11, 27, 19, 30, tzinfo=UTC))  # already closed
    assert not in_entry_window(datetime(2026, 11, 26, 19, 30, tzinfo=UTC))  # holiday
    # Summer: 20:00 UTC close; 16:30 is too early, 19:30 enters.
    assert in_entry_window(datetime(2026, 9, 28, 19, 30, tzinfo=UTC))
    assert not in_entry_window(datetime(2026, 9, 28, 16, 30, tzinfo=UTC))
    # Independence Day eve 2027 (Fri 2 Jul, EDT): 13:00 New York = 17:00 UTC.
    assert in_entry_window(datetime(2027, 7, 2, 16, 30, tzinfo=UTC)) == mc.is_early_close(date(2027, 7, 2))


def test_chain_capture_runs_once_a_day_before_the_close():
    from tradingbot.optionchainsnapshot import capture_due

    def due(y, m, d, hh, mm=45):
        return capture_due(datetime(y, m, d, hh, mm, tzinfo=UTC))[0]

    # Regular days: the 19:45 run, summer (close 20:00 UTC) and winter (21:00 UTC).
    assert due(2026, 9, 28, 19) and not due(2026, 9, 28, 16)
    assert due(2026, 11, 20, 19) and not due(2026, 11, 20, 16)
    # Early close (Nov 27, 18:00 UTC): the 16:45 run captures, 19:45 is after the bell.
    assert due(2026, 11, 27, 16) and not due(2026, 11, 27, 19)
    assert "early close" in capture_due(datetime(2026, 11, 27, 19, 45, tzinfo=UTC))[1]
    # Holiday: neither.
    assert not due(2026, 11, 26, 16) and not due(2026, 11, 26, 19)
