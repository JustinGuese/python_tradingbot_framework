"""Macro event calendar: FRED parsing, idempotent storage, next-event lookups."""

from datetime import date

import httpx
import numpy as np
import pandas as pd
import pytest

from tradingbot.utils import macro_calendar as mc
from tradingbot.utils.db import MacroEvent

_RealClient = httpx.Client


def _fred_client(dates_by_release: dict[int, list[str]]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        rid = int(request.url.params["release_id"])
        assert request.url.params["include_release_dates_with_no_data"] == "true"
        return httpx.Response(
            200, json={"release_dates": [{"release_id": rid, "date": d} for d in dates_by_release[rid]]}
        )

    return _RealClient(transport=httpx.MockTransport(handler))


def test_fomc_table_is_sorted_unique_and_eight_a_year():
    days = mc.FOMC_STATEMENT_DATES
    assert list(days) == sorted(set(days))
    per_year = pd.Series(days).map(lambda d: d.year).value_counts()
    assert per_year.min() >= 8  # scheduled meetings; unscheduled ones add to it
    assert date(2026, 10, 28) in days and date(2020, 3, 15) in days


def test_fetch_fred_release_dates_filters_to_window():
    client = _fred_client({10: ["2025-12-10", "2026-01-13", "2026-02-11", "2031-01-01"]})
    got = mc.fetch_fred_release_dates(10, "2026-01-01", "2030-12-31", "key", client=client)
    assert got == [date(2026, 1, 13), date(2026, 2, 11)]


def test_refresh_is_idempotent(sqlite_db, db_session, monkeypatch):
    releases = {10: ["2026-10-14", "2026-11-12"], 50: ["2026-10-02", "2026-11-06"]}
    monkeypatch.setattr(mc.httpx, "Client", lambda **_: _fred_client(releases))
    counts = mc.refresh_macro_events("key", start="2026-01-01")
    assert counts == {"FOMC": len(mc.FOMC_STATEMENT_DATES), "CPI": 2, "NFP": 2}
    mc.refresh_macro_events("key", start="2026-01-01")
    assert db_session.query(MacroEvent).count() == len(mc.FOMC_STATEMENT_DATES) + 4


@pytest.fixture
def events(sqlite_db, db_session):
    for kind, d in [("CPI", date(2026, 10, 14)), ("FOMC", date(2026, 10, 28)), ("NFP", date(2026, 10, 2))]:
        db_session.add(MacroEvent(kind=kind, event_date=d, source="test"))
    db_session.commit()


def test_next_event_and_business_days(events):
    today = date(2026, 10, 9)  # Friday
    assert mc.next_event(today) == ("CPI", date(2026, 10, 14))
    assert mc.business_days_to_next_event(today) == 3  # Fri, Mon, Tue -> Wed
    assert mc.business_days_to_next_event(date(2026, 10, 14)) == 0
    assert mc.next_event(today, kinds=("NFP",)) is None  # NFP already passed


def test_stale_warning(events):
    assert mc.stale_warning(date(2026, 9, 28)) is None
    assert "refresh" in mc.stale_warning(date(2026, 12, 1))


def test_bdays_to_next_event_series():
    idx = pd.DatetimeIndex(["2026-10-12", "2026-10-14", "2026-10-15", "2026-10-29"])
    got = mc.bdays_to_next_event_series(idx, [date(2026, 10, 14), date(2026, 10, 28)])
    assert got.iloc[:3].tolist() == [2, 0, 9]
    assert np.isnan(got.iloc[3])
