"""Stored earnings timing and ex-dividend dates (utils/corporate_events.py) and the DB-first readers."""

from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from tradingbot.utils import corporate_events as ce
from tradingbot.utils import options
from tradingbot.utils.db import CorporateEventRefresh, DividendEvent, StockEarnings

TODAY = date(2026, 9, 28)


def _ny(day, hh, mm=0):
    return pd.Timestamp(datetime(day.year, day.month, day.day, hh, mm)).tz_localize("America/New_York")


def test_report_timing_from_the_timestamp():
    d = date(2026, 10, 29)
    assert ce.report_after_close(_ny(d, 16, 30)) is True
    assert ce.report_after_close(_ny(d, 8, 0)) is False
    assert ce.report_after_close(_ny(d, 0, 0)) is None  # yfinance's "time unknown"
    # Stored rows are naive UTC: 20:30 UTC is 16:30 New York in October.
    assert ce.report_after_close(datetime(2026, 10, 29, 20, 30)) is True
    assert ce.report_session_date(datetime(2026, 10, 29, 20, 30)) == d


def _earnings_frame(rows):
    idx = pd.DatetimeIndex([ts for ts, *_ in rows])
    return pd.DataFrame(
        {
            "EPS Estimate": [r[1] for r in rows],
            "Reported EPS": [r[2] for r in rows],
            "Surprise(%)": [r[3] for r in rows],
        },
        index=idx,
    )


def test_parse_keeps_eps_and_timing():
    past, nxt = date(2026, 7, 30), date(2026, 10, 29)
    recs = ce.parse_earnings_frame(
        _earnings_frame([(_ny(nxt, 16, 30), 1.6, None, None), (_ny(past, 16, 30), 1.4, 1.5, 7.1)])
    )
    by_day = {ce.report_session_date(r.report_date): r for r in recs}
    assert by_day[past].reported_eps == 1.5 and by_day[past].surprise_pct == 7.1 and by_day[past].after_close
    assert by_day[nxt].reported_eps is None and by_day[nxt].eps_estimate == 1.6


def test_upsert_fills_actuals_and_replaces_an_unknown_time(db_session):
    day = date(2026, 10, 29)
    placeholder = ce.EarningsRecord(ce._naive_utc(_ny(day, 0)), eps_estimate=1.6)
    assert ce.upsert_earnings(db_session, "AAPL", [placeholder]) == (1, 0)
    timed = ce.EarningsRecord(ce._naive_utc(_ny(day, 16, 30)), 1.6, 1.8, 12.5, after_close=True)
    assert ce.upsert_earnings(db_session, "AAPL", [timed]) == (0, 1)
    (row,) = db_session.query(StockEarnings).all()
    assert row.reported_eps == 1.8 and row.surprise_pct == 12.5 and row.after_close is True
    assert ce.report_after_close(row.report_date) is True
    assert ce.upsert_earnings(db_session, "AAPL", [timed]) == (0, 0)  # idempotent


def test_upsert_dividends_paid_replaces_announced_and_moved_dates_go(db_session):
    announced = ce.DividendRecord(date(2026, 11, 9), 0.26, "calendar")
    assert ce.upsert_dividends(db_session, "AAPL", [announced], today=TODAY) == (1, 0)
    moved = ce.DividendRecord(date(2026, 11, 10), 0.26, "calendar")
    ce.upsert_dividends(db_session, "AAPL", [moved], today=TODAY)
    assert [r.ex_date for r in db_session.query(DividendEvent)] == [date(2026, 11, 10)]
    paid = ce.DividendRecord(date(2026, 11, 10), 0.27, "history")
    assert ce.upsert_dividends(db_session, "AAPL", [paid], today=TODAY) == (0, 1)
    (row,) = db_session.query(DividendEvent).all()
    assert (row.amount, row.source) == (0.27, "history")


class FakeTicker:
    def __init__(self, symbol, frame=None, divs=None, calendar=None):
        self.symbol, self._frame, self.dividends, self.calendar = symbol, frame, divs, calendar or {}

    def get_earnings_dates(self, limit=40):
        if self._frame is None:
            raise KeyError("no earnings")  # what yfinance does for an ETF
        return self._frame


@pytest.fixture
def aapl_yf(mocker):
    frame = _earnings_frame(
        [(_ny(date(2026, 10, 29), 16, 30), 1.6, None, None), (_ny(date(2026, 7, 30), 16, 30), 1.4, 1.5, 7.1)]
    )
    divs = pd.Series([0.25, 0.26], index=pd.DatetimeIndex([_ny(date(2026, 5, 11), 0), _ny(date(2026, 8, 10), 0)]))
    tickers = {
        "AAPL": FakeTicker("AAPL", frame, divs, {"Ex-Dividend Date": date(2026, 11, 9)}),
        "SPY": FakeTicker("SPY", None, pd.Series([1.8], index=pd.DatetimeIndex([_ny(date(2026, 9, 19), 0)]))),
    }
    return mocker.patch.object(ce.yf, "Ticker", side_effect=lambda s: tickers[s])


def test_refresh_then_readers_answer_from_the_db(sqlite_db, db_session, aapl_yf, mocker):
    stats = ce.refresh_corporate_events(["AAPL", "SPY"], today=TODAY, delay=0)
    assert stats.failed == [] and stats.with_earnings == {"AAPL"}
    assert {r.symbol for r in db_session.query(CorporateEventRefresh)} == {"AAPL", "SPY"}  # an ETF is refreshed too

    no_yf = mocker.patch.object(options.yf, "Ticker", side_effect=AssertionError("yfinance must not be called"))
    assert options.next_earnings_date("AAPL", TODAY) == date(2026, 10, 29)
    assert options.next_earnings_event("AAPL", TODAY) == (date(2026, 10, 29), True)
    assert options.earnings_history("AAPL", today=TODAY) == [date(2026, 7, 30)]
    assert options.next_dividend("AAPL", TODAY) == (date(2026, 11, 9), 0.26)  # announced, last amount
    assert options.next_dividend("SPY", TODAY) is None  # nothing announced: a real answer
    assert options.earnings_events("SPY", today=TODAY) == []
    no_yf.assert_not_called()


def test_a_past_date_that_never_got_an_actual_eps_is_not_a_report(sqlite_db, db_session):
    today = datetime.now(UTC).date()
    real, meeting, just_reported, scheduled = (today + timedelta(days=n) for n in (-130, -95, -3, 50))

    def row(day, reported):
        return StockEarnings(
            symbol="NVDA", report_date=ce._naive_utc(_ny(day, 16, 20)), eps_estimate=1.0, reported_eps=reported
        )

    db_session.add_all([row(real, 1.1), row(meeting, None), row(just_reported, None), row(scheduled, None)])
    db_session.add(CorporateEventRefresh(symbol="NVDA", refreshed_at=datetime.now(UTC).replace(tzinfo=None)))
    db_session.commit()
    days = [d for d, _ in ce.stored_events("NVDA", today=today).earnings]
    assert days == [real, just_reported, scheduled]  # the meeting went; a fresh report waits for its actual


def test_a_stale_refresh_falls_back_to_yfinance(sqlite_db, db_session, aapl_yf, mocker):
    ce.refresh_corporate_events(["AAPL"], today=TODAY, delay=0)
    ref = db_session.get(CorporateEventRefresh, "AAPL")
    ref.refreshed_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=10)
    db_session.commit()
    assert ce.stored_events("AAPL") is None
    yf_ticker = mocker.patch.object(options.yf, "Ticker", side_effect=lambda s: FakeTicker(s, None, None))
    options.earnings_events("AAPL")
    yf_ticker.assert_called()


def test_stored_events_are_point_in_time():
    """A replayed past day must not see reports from years later as its 'next' or 'last N'."""
    quarters = [date(2019, 1, 30) + timedelta(days=91 * i) for i in range(32)]  # 2019..2026
    stored = ce.StoredEvents(
        earnings=[(d, True) for d in quarters],
        dividends=[(d + timedelta(days=10), 0.2) for d in quarters],
        refreshed_at=datetime(2026, 9, 28),
    )
    day = date(2020, 6, 1)
    assert stored.next_earnings(day)[0] == min(d for d in quarters if d >= day)
    assert stored.history(day, 3) == [d for d in quarters if d < day][-3:]
    assert stored.known_by(day, 8)[-1][0] <= day + timedelta(days=ce.SCHEDULE_HORIZON_DAYS)
    assert stored.known_by(day, 8)[0][0] < day  # the window ends near today, not at the newest row
    assert stored.next_dividend(day)[0] == min(
        d + timedelta(days=10) for d in quarters if d + timedelta(days=10) >= day
    )
