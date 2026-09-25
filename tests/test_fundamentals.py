"""Daily fundamentals capture and the point-in-time as-of reader."""

from datetime import date

import pytest

from tradingbot.utils import fundamentals
from tradingbot.utils.fundamentals import (
    FundamentalsNotAvailable,
    get_fundamentals,
    get_fundamentals_batch,
    parse_info,
    snapshot_fundamentals,
)

INFO = {
    "marketCap": 1_000.0,
    "freeCashflow": 50.0,
    "trailingPE": 25.0,
    "trailingPegRatio": 1.8,  # pegRatio absent -> falls back
    "heldPercentInstitutions": 0.62,
    "enterpriseToEbitda": float("nan"),  # yfinance does emit NaN
    "companyOfficers": [{"name": "x", "age": float("inf")}],
}


@pytest.fixture
def fake_yf(mocker):
    """Replace the network fetch; `payloads` maps symbol -> info dict or Exception."""
    payloads: dict = {}

    def _fetch(symbol):
        value = payloads[symbol]
        if isinstance(value, Exception):
            raise value
        return parse_info(value), value

    mocker.patch.object(fundamentals, "fetch_fundamentals", side_effect=_fetch)
    return payloads


def test_parse_info_maps_keys_and_derives_fcf_yield():
    fields = parse_info(INFO)
    assert fields["market_cap"] == 1_000.0
    assert fields["peg_ratio"] == 1.8
    assert fields["fcf_yield"] == pytest.approx(0.05)
    assert fields["ev_to_ebitda"] is None  # NaN is absence, not a value
    assert fields["forward_pe"] is None


def test_snapshot_is_idempotent_and_json_safe(sqlite_db, fake_yf):
    fake_yf["AAA"] = INFO
    day = date(2026, 9, 25)
    assert snapshot_fundamentals(["AAA"], day, delay_seconds=0)["written"] == ["AAA"]
    fake_yf["AAA"] = dict(INFO, trailingPE=30.0)
    snapshot_fundamentals(["AAA"], day, delay_seconds=0)

    row = get_fundamentals("AAA", day)
    assert row["trailing_pe"] == 30.0  # same-day rerun updated, not duplicated
    assert get_fundamentals_batch(["AAA"], day)["AAA"]["snapshot_date"] == day


def test_failed_and_skipped_are_reported_separately(sqlite_db, fake_yf):
    fake_yf.update({"OK": INFO, "BAD": RuntimeError("429"), "EMPTY": {}})
    result = snapshot_fundamentals(["OK", "BAD", "EMPTY"], date(2026, 9, 25), delay_seconds=0)
    assert result == {"written": ["OK"], "failed": ["BAD"], "skipped": ["EMPTY"]}


def test_as_of_picks_latest_snapshot_on_or_before(sqlite_db, fake_yf):
    for day, pe in ((date(2026, 9, 21), 20.0), (date(2026, 9, 23), 22.0), (date(2026, 9, 25), 24.0)):
        fake_yf["AAA"] = dict(INFO, trailingPE=pe)
        snapshot_fundamentals(["AAA"], day, delay_seconds=0)

    assert get_fundamentals("AAA", date(2026, 9, 24))["trailing_pe"] == 22.0
    assert get_fundamentals("AAA", "2026-09-25 21:00")["trailing_pe"] == 24.0


def test_dates_before_capture_are_not_available(sqlite_db, fake_yf):
    fake_yf["AAA"] = INFO
    snapshot_fundamentals(["AAA"], date(2026, 9, 25), delay_seconds=0)

    with pytest.raises(FundamentalsNotAvailable):
        get_fundamentals("AAA", date(2021, 1, 4))  # a backtest date
    with pytest.raises(FundamentalsNotAvailable):
        get_fundamentals("AAA", date(2026, 10, 30))  # stale beyond max_age_days
    assert get_fundamentals_batch(["AAA", "ZZZ"], date(2021, 1, 4)) == {"AAA": None, "ZZZ": None}
