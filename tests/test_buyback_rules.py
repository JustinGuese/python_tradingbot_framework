from datetime import UTC, date, datetime, timedelta

from tradingbot.utils.buyback_rules import (
    active_symbols,
    dedupe_recent,
    entry_session,
    has_item_801_or_701,
    matches_buyback,
)
from tradingbot.utils.db import BuybackEvent
from tradingbot.utils.market_calendar import next_session


def test_matches_buyback_and_items():
    # Hits authorization regex
    assert matches_buyback("board authorized repurchase of up to $5 billion")
    assert matches_buyback("Company announced a share repurchase program")
    assert matches_buyback("repurchase of up to 10 million shares")

    # Rejects non-authorizations
    assert not matches_buyback("no repurchase program")
    assert not matches_buyback("quarterly financial results press release")
    assert not matches_buyback("")
    assert not matches_buyback(None)

    # 8-K items check
    assert has_item_801_or_701(["Item 8.01"])
    assert has_item_801_or_701(["Item 7.01", "Item 5.02"])
    assert has_item_801_or_701("8.01, 9.01")
    assert not has_item_801_or_701(["Item 1.01", "Item 2.01"])
    assert not has_item_801_or_701(None)


def test_entry_session_after_close():
    # 2024-01-08 is Monday (session close 16:00 EST = 21:00 UTC)
    # 15:00 UTC is during market hours -> enters same day (Monday)
    filed_midday = datetime(2024, 1, 8, 15, 0, tzinfo=UTC)
    assert entry_session(filed_midday) == date(2024, 1, 8)

    # 21:05 UTC is strictly after close -> enters next session (Tuesday)
    filed_after_close = datetime(2024, 1, 8, 21, 5, tzinfo=UTC)
    assert entry_session(filed_after_close) == date(2024, 1, 9)


def test_entry_session_friday_evening():
    # 2024-01-19 is Friday (regular). Filing at 22:00 UTC (after close) -> enters Monday 2024-01-22
    filed_friday = datetime(2024, 1, 19, 22, 0, tzinfo=UTC)
    assert entry_session(filed_friday) == date(2024, 1, 22)

    # 2024-01-12 is Friday before MLK Day (holiday on Mon 2024-01-15) -> enters Tuesday 2024-01-16
    filed_pre_holiday = datetime(2024, 1, 12, 22, 0, tzinfo=UTC)
    assert entry_session(filed_pre_holiday) == date(2024, 1, 16)


def test_hold_window_ten_sessions_across_holiday():
    # Event enters on Friday 2024-01-12. Monday 2024-01-15 is MLK Day holiday.
    event = {"symbol": "AAPL", "entry_session": date(2024, 1, 12)}

    expected_sessions = [
        date(2024, 1, 12),  # session 0 (Fri)
        date(2024, 1, 16),  # session 1 (Tue)
        date(2024, 1, 17),  # session 2 (Wed)
        date(2024, 1, 18),  # session 3 (Thu)
        date(2024, 1, 19),  # session 4 (Fri)
        date(2024, 1, 22),  # session 5 (Mon)
        date(2024, 1, 23),  # session 6 (Tue)
        date(2024, 1, 24),  # session 7 (Wed)
        date(2024, 1, 25),  # session 8 (Thu)
        date(2024, 1, 26),  # session 9 (Fri)
    ]

    # Verify active on each of the 10 held sessions
    for d in expected_sessions:
        assert active_symbols([event], d) == ["AAPL"]

    # Verify exited on the 11th session (session index 10)
    session_10 = next_session(expected_sessions[-1])
    assert session_10 == date(2024, 1, 29)
    assert active_symbols([event], session_10) == []

    # Verify deduplication when multiple events for same symbol exist
    event2 = {"symbol": "AAPL", "entry_session": date(2024, 1, 16)}
    assert active_symbols([event, event2], date(2024, 1, 16)) == ["AAPL"]


def test_dedupe_recent_30_days():
    base = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
    events = [
        {"symbol": "AAPL", "filed_at": base, "id": 1},
        {"symbol": "AAPL", "filed_at": base + timedelta(days=10), "id": 2},  # drop (< 30d)
        {"symbol": "MSFT", "filed_at": base + timedelta(days=15), "id": 3},  # keep (diff sym)
        {"symbol": "AAPL", "filed_at": base + timedelta(days=31), "id": 4},  # keep (>= 30d)
        {"symbol": "AAPL", "filed_at": base + timedelta(days=45), "id": 5},  # drop (< 30d from id 4)
    ]

    kept = dedupe_recent(events, window_days=30)
    kept_ids = [e["id"] for e in kept]
    assert kept_ids == [1, 3, 4]


def test_buyback_event_model():
    assert BuybackEvent.__tablename__ == "buyback_events"
    cols = {c.name for c in BuybackEvent.__table__.columns}
    expected_cols = {"id", "symbol", "accession", "filed_at", "source_url", "snippet", "created_at"}
    assert expected_cols.issubset(cols)
    # Confirm unique constraint on accession
    unique_cols = {c.name for uq in BuybackEvent.__table__.constraints if hasattr(uq, "columns") for c in uq.columns}
    assert "accession" in unique_cols
