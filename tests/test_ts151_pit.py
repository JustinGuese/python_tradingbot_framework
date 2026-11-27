"""Unit tests for TS151 point-in-time data ingestion pipelines (PLAN-ts151.md S1, S2, S3).

Covers:
- S1: SEC XBRL fundamentals point-in-time filtering, restatement handling, and parsing.
- S2: CFTC Commitments of Traders (COT) point-in-time release lag and market verification.
- S3: EDGAR merger / tender offer announcements, header parsing, and target CIK resolution.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy.orm import sessionmaker

from tradingbot.utils.db import CotPosition, MergerEvent, SecFundamental
from tradingbot.utils.ts151 import cot, data_api, merger_scan, sec_fundamentals

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def mock_db_session(monkeypatch, test_engine):
    """Patch get_db_session in TS151 modules to point to the in-memory test_engine."""
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

    monkeypatch.setattr("tradingbot.utils.ts151.sec_fundamentals.get_db_session", _session)
    monkeypatch.setattr("tradingbot.utils.ts151.cot.get_db_session", _session)
    monkeypatch.setattr("tradingbot.utils.ts151.merger_scan.get_db_session", _session)
    monkeypatch.setattr("tradingbot.utils.ts151.data_api.get_db_session", _session)
    return _session


# ==============================================================================
# S1: SEC Fundamentals Tests
# ==============================================================================


def test_pit_latest_excludes_future_filing():
    """pit_latest must exclude a fact filed after as_of even if period_end is in the past."""
    # Fact 1: Q1 2025 ended 2025-03-31, filed on 2025-04-15 (valid)
    # Fact 2: Q2 2025 ended 2025-06-30, filed on 2025-07-20 (filed in future relative to as_of)
    frame = pd.DataFrame(
        [
            {
                "cik": 320193,
                "ticker": "AAPL",
                "tag": "StockholdersEquity",
                "unit": "USD",
                "period_end": pd.to_datetime("2025-03-31"),
                "filed": pd.to_datetime("2025-04-15"),
                "value": 50000.0,
                "form": "10-Q",
                "fy": 2025,
                "fp": "Q1",
            },
            {
                "cik": 320193,
                "ticker": "AAPL",
                "tag": "StockholdersEquity",
                "unit": "USD",
                "period_end": pd.to_datetime("2025-06-30"),
                "filed": pd.to_datetime("2025-07-20"),
                "value": 60000.0,
                "form": "10-Q",
                "fy": 2025,
                "fp": "Q2",
            },
        ]
    )

    # As of 2025-05-01: Q2 filing (filed 2025-07-20) must NOT be visible
    as_of = pd.Timestamp("2025-05-01")
    pit = data_api.pit_latest(frame, as_of)
    assert not pit.empty
    assert "AAPL" in pit.index
    assert pit.loc["AAPL", "StockholdersEquity"] == 50000.0

    # As of 2025-08-01: Q2 filing is now visible and supersedes Q1
    pit_later = data_api.pit_latest(frame, pd.Timestamp("2025-08-01"))
    assert pit_later.loc["AAPL", "StockholdersEquity"] == 60000.0


def test_pit_quarterly_keeps_original_value_before_restatement():
    """pit_quarterly keeps the original reported value until a restatement is filed."""
    # Original 10-Q filed 2025-04-15 with EPS 1.50
    # Restated 10-Q/A filed 2025-09-10 with EPS 1.40 for the same period_end 2025-03-31
    frame = pd.DataFrame(
        [
            {
                "cik": 320193,
                "ticker": "AAPL",
                "tag": "EarningsPerShareDiluted",
                "unit": "USD/shares",
                "period_end": pd.to_datetime("2025-03-31"),
                "filed": pd.to_datetime("2025-04-15"),
                "value": 1.50,
                "form": "10-Q",
                "fy": 2025,
                "fp": "Q1",
            },
            {
                "cik": 320193,
                "ticker": "AAPL",
                "tag": "EarningsPerShareDiluted",
                "unit": "USD/shares",
                "period_end": pd.to_datetime("2025-03-31"),
                "filed": pd.to_datetime("2025-09-10"),
                "value": 1.40,
                "form": "10-Q/A",
                "fy": 2025,
                "fp": "Q1",
            },
        ]
    )

    # Before restatement (as of 2025-05-01), the series contains the original 1.50
    s_before = data_api.pit_quarterly(frame, "AAPL", "EarningsPerShareDiluted", pd.Timestamp("2025-05-01"))
    assert len(s_before) == 1
    assert s_before.iloc[0] == 1.50

    # After restatement (as of 2025-10-01), the latest filing (10-Q/A) supersedes
    s_after = data_api.pit_quarterly(frame, "AAPL", "EarningsPerShareDiluted", pd.Timestamp("2025-10-01"))
    assert len(s_after) == 1
    assert s_after.iloc[0] == 1.40


def test_parse_company_facts_extraction_and_revenue_fallback():
    """parse_company_facts extracts standard tags and falls back to ASC 606 revenue concept."""
    mock_companyfacts = {
        "cik": 320193,
        "entityName": "Apple Inc.",
        "facts": {
            "us-gaap": {
                "StockholdersEquity": {
                    "units": {
                        "USD": [
                            {
                                "end": "2024-09-28",
                                "val": 66700000000,
                                "fy": 2024,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-11-01",
                            },
                            {
                                "end": "2024-09-28",
                                "val": 66700000000,
                                "form": "8-K",  # Disallowed form -> should be ignored
                                "filed": "2024-11-01",
                            },
                        ]
                    }
                },
                # Primary Revenues is missing; fallback should be used under tag 'Revenues'
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            {
                                "end": "2024-09-28",
                                "val": 391035000000,
                                "fy": 2024,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-11-01",
                            }
                        ]
                    }
                },
            },
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {
                                "end": "2024-10-18",
                                "val": 15115822000,
                                "form": "10-K",
                                "filed": "2024-11-01",
                            }
                        ]
                    }
                }
            },
        },
    }

    parsed = sec_fundamentals.parse_company_facts(mock_companyfacts, symbol="AAPL", cik=320193)
    assert len(parsed) == 3

    tags = {r["tag"]: r["value"] for r in parsed}
    assert tags["StockholdersEquity"] == 66700000000.0
    assert tags["Revenues"] == 391035000000.0
    assert tags["EntityCommonStockSharesOutstanding"] == 15115822000.0


def test_upsert_sec_fundamentals(mock_db_session):
    """Test idempotent upsert into ts151_sec_fundamentals."""
    test_facts = [
        {
            "cik": 1000,
            "ticker": "TEST",
            "tag": "StockholdersEquity",
            "unit": "USD",
            "period_end": date(2025, 3, 31),
            "filed": date(2025, 4, 15),
            "value": 12345.0,
            "form": "10-Q",
            "fy": 2025,
            "fp": "Q1",
        }
    ]

    count1 = sec_fundamentals.upsert_sec_fundamentals(test_facts)
    assert count1 == 1

    # Update value idempotently
    test_facts[0]["value"] = 54321.0
    count2 = sec_fundamentals.upsert_sec_fundamentals(test_facts)
    assert count2 == 1

    with mock_db_session() as session:
        row = (
            session.query(SecFundamental)
            .filter_by(cik=1000, tag="StockholdersEquity", period_end=date(2025, 3, 31))
            .first()
        )
        assert row is not None
        assert row.value == 54321.0
        # Clean up
        session.delete(row)


# ==============================================================================
# S2: CFTC COT Tests
# ==============================================================================


def test_cot_asof_release_lag():
    """cot_asof hides a Tuesday report on Wed/Thu and displays it from Friday with lag=3."""
    # Tuesday report date: 2026-03-03
    frame = pd.DataFrame(
        [
            {
                "market_code": "088691",
                "report_date": pd.to_datetime("2026-03-03"),  # Tuesday
                "market_name": "GOLD - COMMODITY EXCHANGE INC.",
                "open_interest": 500000.0,
                "comm_long": 100000.0,
                "comm_short": 200000.0,
                "noncomm_long": 250000.0,
                "noncomm_short": 50000.0,
                "nonrep_long": 50000.0,
                "nonrep_short": 50000.0,
            }
        ]
    )

    # Wednesday (lag 1): hidden
    as_of_wed = pd.Timestamp("2026-03-04")
    assert data_api.cot_asof(frame, as_of_wed, release_lag_days=3).empty

    # Thursday (lag 2): hidden
    as_of_thu = pd.Timestamp("2026-03-05")
    assert data_api.cot_asof(frame, as_of_thu, release_lag_days=3).empty

    # Friday (lag 3): released / visible
    as_of_fri = pd.Timestamp("2026-03-06")
    df_fri = data_api.cot_asof(frame, as_of_fri, release_lag_days=3)
    assert len(df_fri) == 1
    assert df_fri.iloc[0]["open_interest"] == 500000.0

    # Saturday (lag 4): visible
    as_of_sat = pd.Timestamp("2026-03-07")
    assert len(data_api.cot_asof(frame, as_of_sat, release_lag_days=3)) == 1


def test_verify_cot_market_names():
    """verify_cot_market_names confirms expected commodity keywords and raises on mismatch."""
    valid_records = [
        {"market_code": "067651", "market_name": "LIGHT SWEET CRUDE OIL - NEW YORK MERCANTILE EXCHANGE"},
        {"market_code": "088691", "market_name": "GOLD - COMMODITY EXCHANGE INC."},
        {"market_code": "002602", "market_name": "CORN - CHICAGO BOARD OF TRADE"},
        {"market_code": "080732", "market_name": "SUGAR NO. 11 - ICE FUTURES U.S."},
    ]
    # Valid records must not raise
    cot.verify_cot_market_names(valid_records)

    # Mismatch record should raise ValueError
    invalid_record = [{"market_code": "088691", "market_name": "SILVER - COMMODITY EXCHANGE INC."}]
    with pytest.raises(ValueError, match="CFTC market name mismatch for code 088691"):
        cot.verify_cot_market_names(invalid_record)


def test_upsert_cot_positions(mock_db_session):
    """Test idempotent upsert into ts151_cot_positions."""
    test_records = [
        {
            "market_code": "999999",
            "report_date": date(2026, 3, 3),
            "market_name": "TEST COMMODITY",
            "open_interest": 1000.0,
            "comm_long": 100.0,
            "comm_short": 200.0,
            "noncomm_long": 300.0,
            "noncomm_short": 400.0,
            "nonrep_long": 500.0,
            "nonrep_short": 600.0,
        }
    ]

    count1 = cot.upsert_cot_positions(test_records)
    assert count1 == 1

    # Update open_interest
    test_records[0]["open_interest"] = 2000.0
    count2 = cot.upsert_cot_positions(test_records)
    assert count2 == 1

    with mock_db_session() as session:
        row = session.query(CotPosition).filter_by(market_code="999999", report_date=date(2026, 3, 3)).first()
        assert row is not None
        assert row.open_interest == 2000.0
        # Clean up
        session.delete(row)


# ==============================================================================
# S3: EDGAR Merger Announcements Tests
# ==============================================================================


def test_parse_subject_company_from_sc_to_t_header():
    """Verify subject CIK and company name extraction from SC TO-T submission header fixture."""
    fixture_path = FIXTURES_DIR / "ts151_sc_to_t_header.txt"
    assert fixture_path.exists(), f"Fixture not found: {fixture_path}"

    header_text = fixture_path.read_text(encoding="utf-8")
    parsed = merger_scan.parse_subject_company_from_header(header_text)
    assert parsed is not None

    subject_cik, subject_name = parsed
    # Target must be SEQUANS COMMUNICATIONS (CIK 1383395), NOT Renesas Electronics Corp (1774474)
    assert subject_cik == 1383395
    assert "SEQUANS COMMUNICATIONS" in subject_name

    acceptance_dt = merger_scan.parse_header_acceptance_datetime(header_text)
    assert acceptance_dt is not None
    assert acceptance_dt == datetime(2024, 1, 5, 7, 54, 21)


def test_parse_form_index_lines():
    """parse_form_index_lines filters for target forms and extracts accession correctly."""
    sample_index = (
        "Description:           Daily Index of EDGAR Dissemination Feed\n"
        "Last Data Received:    Jan  5, 2024\n"
        "\n"
        "Form Type   Company Name                                                  CIK         Date Filed  File Name\n"
        "---------------------------------------------------------------------------------------------------------------------------------------------\n"
        "10-K        APPLE INC                                                     320193      20240105    edgar/data/320193/0000320193-24-000001.txt\n"
        "SC TO-T/A   Renesas Electronics Corp                                      1774474     20240105    edgar/data/1774474/0001193125-24-002883.txt\n"
        "SC 14D9     SEQUANS COMMUNICATIONS                                        1383395     20240105    edgar/data/1383395/0001193125-24-002891.txt\n"
        "DEFM14A     TARGET MERGER CO                                              987654      20240105    edgar/data/987654/0000987654-24-000123.txt\n"
    )

    candidates = merger_scan.parse_form_index_lines(sample_index)
    assert len(candidates) == 3

    forms = [c["base_form"] for c in candidates]
    assert forms == ["SC TO-T", "SC 14D9", "DEFM14A"]

    assert candidates[0]["accession"] == "0001193125-24-002883"
    assert candidates[1]["accession"] == "0001193125-24-002891"
    assert candidates[2]["accession"] == "0000987654-24-000123"


def test_resolve_filing_target_defm14a():
    """For DEFM14A, the filer is the target company."""
    cand = {
        "form": "DEFM14A",
        "base_form": "DEFM14A",
        "company_name": "ACQUIRED CORP",
        "cik": 123456,
        "filing_date": "2025-06-01",
        "file_name": "edgar/data/123456/0000123456-25-000001.txt",
        "accession": "0000123456-25-000001",
    }
    target = merger_scan.resolve_filing_target(cand)
    assert target is not None
    assert target["cik"] == 123456
    assert target["company_name"] == "ACQUIRED CORP"
    assert target["filed_at"] == datetime(2025, 6, 1, 23, 59, 59)


def test_merger_heartbeat_and_upsert(mock_db_session):
    """Test touch_heartbeat and idempotent upsert into ts151_merger_events."""
    now_stamp = datetime(2026, 11, 27, 10, 0, 0)
    merger_scan.touch_heartbeat(now=now_stamp)

    with mock_db_session() as session:
        hb = session.query(MergerEvent).filter_by(accession="_HEARTBEAT").first()
        assert hb is not None
        assert hb.filed_at == now_stamp

    # Test event upsert
    event = {
        "accession": "0001193125-24-999999",
        "cik": 1383395,
        "ticker": "SQNS",
        "company_name": "SEQUANS COMMUNICATIONS",
        "form": "SC TO-T",
        "filed_at": datetime(2024, 1, 5, 7, 54, 21),
    }
    count = merger_scan.upsert_merger_events([event])
    assert count == 1

    with mock_db_session() as session:
        row = session.query(MergerEvent).filter_by(accession="0001193125-24-999999").first()
        assert row is not None
        assert row.ticker == "SQNS"
        assert row.cik == 1383395
        # Clean up
        session.delete(row)
        hb_row = session.query(MergerEvent).filter_by(accession="_HEARTBEAT").first()
        if hb_row:
            session.delete(hb_row)
