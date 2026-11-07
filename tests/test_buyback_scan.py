"""
Tests for SEC EDGAR 8-K buyback scanner (tradingbot.utils.buyback_scan and tradingbot.buybackscan).
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import sessionmaker

import tradingbot.utils.buyback_scan as scan_mod
from tradingbot.buybackscan import main as cli_main
from tradingbot.utils.buyback_scan import (
    TokenBucket,
    clear_cik_cache,
    parse_candidate_filings,
    scan,
)
from tradingbot.utils.db import BuybackEvent

CIK_MAPPING_PAYLOAD = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp."},
    "2": {"cik_str": 1018724, "ticker": "AMZN", "title": "Amazon Com Inc."},
    "3": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
    "4": {"cik_str": 1045810, "ticker": "NVDA", "title": "Nvidia Corp."},
}

AAPL_SUBMISSIONS_FIXTURE = {
    "cik": "0000320193",
    "filings": {
        "recent": {
            "accessionNumber": [
                "0000320193-24-000069",  # Valid 8-K Item 8.01 with buyback (recent)
                "0000320193-24-000068",  # Valid 8-K Item 8.01 without buyback (recent)
                "0000320193-24-000067",  # 8-K but Item 1.01 only (skip)
                "0000320193-24-000050",  # 8-K Item 8.01 but older than 5 days (skip)
                "0000320193-24-000049",  # 10-Q form (skip)
            ],
            "filingDate": [
                "2024-05-02",
                "2024-05-01",
                "2024-05-02",
                "2024-04-15",
                "2024-05-02",
            ],
            "acceptanceDateTime": [
                "2024-05-02T16:30:15.000Z",
                "2024-05-01T10:00:00.000Z",
                "2024-05-02T12:00:00.000Z",
                "2024-04-15T16:00:00.000Z",
                "2024-05-02T17:00:00.000Z",
            ],
            "form": [
                "8-K",
                "8-K",
                "8-K",
                "8-K",
                "10-Q",
            ],
            "items": [
                ["8.01"],
                ["8.01"],
                ["1.01"],
                ["8.01"],
                [],
            ],
            "primaryDocument": [
                "aapl-buyback.htm",
                "aapl-lease.htm",
                "aapl-credit.htm",
                "aapl-old.htm",
                "aapl-10q.htm",
            ],
        }
    },
}

AAPL_BUYBACK_DOC_HTML = """
<html>
<body>
<h1>Item 8.01 Other Events</h1>
<p>
On May 2, 2024, the Board of Directors authorized a share repurchase program
of up to $110 billion of the Company's common stock.
</p>
</body>
</html>
"""

AAPL_LEASE_DOC_HTML = """
<html>
<body>
<h1>Item 8.01 Other Events</h1>
<p>
The Company entered into an amended office lease agreement for premises in Austin, Texas.
</p>
</body>
</html>
"""


@pytest.fixture
def sqlite_buyback_db(test_engine, monkeypatch):
    """Route DB sessions to the test SQLite database and disable rate limiting delay."""
    Session = sessionmaker(bind=test_engine)

    @contextmanager
    def _session():
        session = Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(scan_mod, "get_db_session", _session)
    monkeypatch.setattr(scan_mod, "DEFAULT_RATE_LIMITER", TokenBucket(rate=0))
    clear_cik_cache()
    yield _session
    clear_cik_cache()


def _mock_urlopen_factory(raising_symbols: set[str] | None = None):
    """Create a mock urllib.request.urlopen handler for EDGAR endpoints."""
    raising = raising_symbols or set()

    def _mock_urlopen(req, timeout=15.0):
        url = req.get_full_url() if hasattr(req, "get_full_url") else req.full_url

        if "company_tickers.json" in url:
            return io.BytesIO(json.dumps(CIK_MAPPING_PAYLOAD).encode("utf-8"))

        for sym in raising:
            cik_int = CIK_MAPPING_PAYLOAD.get(sym, {}).get("cik_str")
            if not cik_int:
                for v in CIK_MAPPING_PAYLOAD.values():
                    if v["ticker"] == sym:
                        cik_int = v["cik_str"]
            if cik_int and f"CIK{str(cik_int).zfill(10)}.json" in url:
                raise urllib.error.URLError(f"Connection reset for {sym}")

        if "CIK0000320193.json" in url:
            return io.BytesIO(json.dumps(AAPL_SUBMISSIONS_FIXTURE).encode("utf-8"))

        # Empty submissions for other symbols
        if "submissions/CIK" in url:
            empty_sub = {"cik": "0", "filings": {"recent": {"form": [], "filingDate": []}}}
            return io.BytesIO(json.dumps(empty_sub).encode("utf-8"))

        if "aapl-buyback.htm" in url:
            return io.BytesIO(AAPL_BUYBACK_DOC_HTML.encode("utf-8"))

        if "aapl-lease.htm" in url:
            return io.BytesIO(AAPL_LEASE_DOC_HTML.encode("utf-8"))

        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    return _mock_urlopen


def test_parse_submissions_fixture():
    """Verify parsing a saved submissions JSON fixture filters 8-Ks with Item 8.01/7.01 in window."""
    cutoff_dt = datetime(2024, 4, 30, 0, 0, 0)
    candidates = parse_candidate_filings(
        submissions_data=AAPL_SUBMISSIONS_FIXTURE,
        symbol="AAPL",
        cik=320193,
        cutoff_dt=cutoff_dt,
    )

    # Should keep only the 2 recent 8-Ks with Item 8.01 (buyback and lease),
    # skipping Item 1.01, older filing (2024-04-15), and 10-Q
    accessions = [c["accession"] for c in candidates]
    assert accessions == ["0000320193-24-000069", "0000320193-24-000068"]
    assert candidates[0]["primary_doc"] == "aapl-buyback.htm"
    assert candidates[1]["primary_doc"] == "aapl-lease.htm"
    assert candidates[0]["filed_at"] == datetime(2024, 5, 2, 16, 30, 15)


def test_scan_inserts_matching_8k_once_and_not_twice(sqlite_buyback_db, monkeypatch):
    """Scan inserts matching 8-K once; running again is idempotent and inserts zero duplicates."""
    monkeypatch.setattr(urllib.request, "urlopen", _mock_urlopen_factory())

    now = datetime(2024, 5, 3, 12, 0, 0, tzinfo=UTC)

    # First run: should find and insert the matching AAPL buyback 8-K
    inserted = scan(symbols=["AAPL"], since_days=5, now=now)
    assert len(inserted) == 1
    event = inserted[0]
    assert event.symbol == "AAPL"
    assert event.accession == "0000320193-24-000069"
    assert event.filed_at == datetime(2024, 5, 2, 16, 30, 15)
    assert "$110 billion" in (event.snippet or "")
    assert event.source_url == "https://www.sec.gov/Archives/edgar/data/320193/000032019324000069/aapl-buyback.htm"

    with sqlite_buyback_db() as session:
        rows = session.query(BuybackEvent).all()
        assert len(rows) == 1
        assert rows[0].accession == "0000320193-24-000069"

    # Second run: should recognize existing accession and insert nothing
    inserted_second = scan(symbols=["AAPL"], since_days=5, now=now)
    assert len(inserted_second) == 0

    with sqlite_buyback_db() as session:
        rows = session.query(BuybackEvent).all()
        assert len(rows) == 1


def test_scan_skips_non_matching_text(sqlite_buyback_db, monkeypatch):
    """An 8-K filing without buyback authorization language in its primary doc is skipped."""
    monkeypatch.setattr(urllib.request, "urlopen", _mock_urlopen_factory())

    # Submissions fixture contains aapl-lease.htm which has Item 8.01 but no buyback terms.
    # After a scan, aapl-lease.htm must NOT be present in the database.
    now = datetime(2024, 5, 3, 12, 0, 0, tzinfo=UTC)
    scan(symbols=["AAPL"], since_days=5, now=now)

    with sqlite_buyback_db() as session:
        lease_row = session.query(BuybackEvent).filter_by(accession="0000320193-24-000068").first()
        assert lease_row is None


def test_scan_tolerates_one_symbol_raising(sqlite_buyback_db, monkeypatch):
    """If one symbol raises an exception (<= 20% fail rate), it is logged and skipped while others succeed."""
    # 5 symbols, 1 failing (MSFT) -> 1/5 = 20% failure, within allowed threshold
    symbols = ["AAPL", "MSFT", "AMZN", "GOOGL", "NVDA"]
    monkeypatch.setattr(urllib.request, "urlopen", _mock_urlopen_factory(raising_symbols={"MSFT"}))

    now = datetime(2024, 5, 3, 12, 0, 0, tzinfo=UTC)
    inserted = scan(symbols=symbols, since_days=5, now=now)

    # AAPL succeeded and inserted its event
    assert len(inserted) == 1
    assert inserted[0].symbol == "AAPL"

    with sqlite_buyback_db() as session:
        assert session.query(BuybackEvent).count() == 1


def test_scan_high_failure_rate_and_cli_exit(sqlite_buyback_db, monkeypatch):
    """If more than 20% of symbols fail, scan raises RuntimeError and cli wrapper exits non-zero (1)."""
    # 2 symbols, 1 failing (MSFT) -> 1/2 = 50% > 20% failure threshold
    symbols = ["AAPL", "MSFT"]
    monkeypatch.setattr(urllib.request, "urlopen", _mock_urlopen_factory(raising_symbols={"MSFT"}))
    monkeypatch.setattr("tradingbot.buybackscan.init_db", lambda: None)
    monkeypatch.setattr("tradingbot.buybackscan.SP100", ("AAPL", "MSFT"))

    now = datetime(2024, 5, 3, 12, 0, 0, tzinfo=UTC)
    with pytest.raises(RuntimeError, match="Buyback scan failed"):
        scan(symbols=symbols, since_days=5, now=now)

    # CLI wrapper main() catches failure and returns 1
    ret = cli_main(["--backfill-days", "5"])
    assert ret == 1
