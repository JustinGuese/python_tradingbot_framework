"""utils/dolthub_options.py: mapping DoltHub's raw option_chain rows to OptionQuote rows."""

from datetime import date, datetime

import pytest

from tradingbot.utils import dolthub_options as dh


def test_occ_symbol_matches_options_occ_format():
    # Cross-checked against options.OCC_RE / the live yfinance format, e.g. AAPL251017C00150000.
    assert dh.occ_symbol("AAPL", date(2025, 10, 17), "C", 150.0) == "AAPL251017C00150000"
    assert dh.occ_symbol("SPY", date(2019, 2, 15), "P", 65.5) == "SPY190215P00065500"


def test_to_option_quote_rows_maps_call_put_and_fills_spot():
    raw = [
        {
            "date": "2024-06-03",
            "act_symbol": "AAPL",
            "expiration": "2024-07-19",
            "strike": "195.00",
            "call_put": "Call",
            "bid": "5.10",
            "ask": "5.30",
            "vol": "0.2411",
            "delta": "0.55",
            "gamma": "0.01",
            "theta": "-0.05",
            "vega": "0.20",
            "rho": "0.02",
        },
        {
            "date": "2024-06-03",
            "act_symbol": "AAPL",
            "expiration": "2024-07-19",
            "strike": "195.00",
            "call_put": "Put",
            "bid": "4.00",
            "ask": "4.20",
            "vol": "0.25",
            "delta": "-0.45",
            "gamma": "0.01",
            "theta": "-0.04",
            "vega": "0.19",
            "rho": "-0.02",
        },
    ]
    rows = dh.to_option_quote_rows(raw, {date(2024, 6, 3): 194.5})

    call, put = rows
    assert call["contract_symbol"] == "AAPL240719C00195000"
    assert call["option_type"] == "C"
    assert put["contract_symbol"] == "AAPL240719P00195000"
    assert put["option_type"] == "P"
    for row in rows:
        assert row["underlying"] == "AAPL"
        assert row["strike"] == 195.0
        assert row["underlying_price"] == 194.5
        assert row["expiration"] == datetime(2024, 7, 19)
        assert row["snapshot_at"] == datetime(2024, 6, 3, 21, 0)
        assert row["volume"] is None and row["open_interest"] is None and row["last_price"] is None
    assert call["bid"] == 5.10 and call["ask"] == 5.30 and call["implied_volatility"] == pytest.approx(0.2411)


def test_to_option_quote_rows_keeps_row_when_spot_missing():
    raw = [
        {
            "date": "2024-06-04",
            "act_symbol": "AAPL",
            "expiration": "2024-07-19",
            "strike": "195.00",
            "call_put": "Call",
            "bid": "5.10",
            "ask": "5.30",
            "vol": "0.24",
        }
    ]
    rows = dh.to_option_quote_rows(raw, {})  # no price for 2024-06-04
    assert rows[0]["underlying_price"] is None
    assert rows[0]["bid"] == 5.10  # the rest of the row is still usable


def test_to_option_quote_rows_handles_null_bid_ask_iv():
    raw = [
        {
            "date": "2024-06-03",
            "act_symbol": "AAPL",
            "expiration": "2024-07-19",
            "strike": "195.00",
            "call_put": "Put",
            "bid": None,
            "ask": None,
            "vol": None,
        }
    ]
    rows = dh.to_option_quote_rows(raw, {date(2024, 6, 3): 194.5})
    assert rows[0]["bid"] is None and rows[0]["ask"] is None and rows[0]["implied_volatility"] is None


def test_fetch_chain_days_batches_and_dedupes_dates(monkeypatch):
    calls: list[str] = []

    def fake_run_query(sql: str, **_kwargs):
        calls.append(sql)
        return [{"sql": sql}]

    monkeypatch.setattr(dh, "_run_query", fake_run_query)
    days = [date(2024, 6, 3), date(2024, 6, 4), date(2024, 6, 3)]  # duplicate on purpose
    rows = dh.fetch_chain_days("AAPL", days, batch_size=1)

    assert len(calls) == 2  # deduped to 2 distinct days, batch size 1 -> 2 queries
    assert all("act_symbol = 'AAPL'" in c for c in calls)
    assert len(rows) == 2


def test_run_query_raises_after_exhausting_retries(monkeypatch):
    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"query_execution_status": "Error", "query_execution_message": "boom"}

    monkeypatch.setattr(dh.requests, "get", lambda *a, **k: _FakeResp())
    monkeypatch.setattr(dh.time, "sleep", lambda _s: None)
    with pytest.raises(dh.DoltHubQueryError, match="boom"):
        dh._run_query("SELECT 1", retries=1)


def test_run_query_returns_rows_on_success(monkeypatch):
    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"query_execution_status": "Success", "rows": [{"a": 1}]}

    monkeypatch.setattr(dh.requests, "get", lambda *a, **k: _FakeResp())
    assert dh._run_query("SELECT 1") == [{"a": 1}]


def test_run_query_raises_row_limit_immediately_without_retrying(monkeypatch):
    calls = []

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"query_execution_status": "Error", "query_execution_message": "RowLimit"}

    def fake_get(*a, **k):
        calls.append(1)
        return _FakeResp()

    monkeypatch.setattr(dh.requests, "get", fake_get)
    monkeypatch.setattr(dh.time, "sleep", lambda _s: (_ for _ in ()).throw(AssertionError("should not sleep/retry")))
    with pytest.raises(dh.DoltHubRowLimitError):
        dh._run_query("SELECT 1")
    assert len(calls) == 1  # one attempt, no retry -- retrying an identical query can't fix RowLimit


def test_fetch_chain_days_splits_batch_on_row_limit(monkeypatch):
    """A batch that trips RowLimit is halved and retried, recursing down to single days."""
    days = [date(2020, 3, d) for d in (9, 10, 11, 12, 13)]
    calls: list[tuple[date, ...]] = []

    def fake_run_query(sql: str, **_kwargs):
        # Recover which days this call's IN(...) covers from the SQL text.
        batch = tuple(d for d in days if d.isoformat() in sql)
        calls.append(batch)
        if len(batch) > 2:
            raise dh.DoltHubRowLimitError("RowLimit")
        return [{"day": d.isoformat()} for d in batch]

    monkeypatch.setattr(dh, "_run_query", fake_run_query)
    rows = dh.fetch_chain_days("AAPL", days, batch_size=5)

    assert {r["day"] for r in rows} == {d.isoformat() for d in days}
    assert len(calls) > 1  # the oversized batch was split, not answered in one call
    assert calls[0] == tuple(days)  # the first attempt is always the full batch
