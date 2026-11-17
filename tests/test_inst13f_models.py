"""
Tests for Form 13F database models and signal interface stubs (Batch 0).
"""

from datetime import date, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from tradingbot.utils.db import CusipMap, Inst13fFiling, Inst13fHolding


def test_models_create_on_empty_sqlite(db_session):
    """Verify that 13F models create on an empty SQLite database and persist records."""
    filing = Inst13fFiling(
        accession="0001067983-24-000001",
        cik=1067983,
        filer_name="BERKSHIRE HATHAWAY INC",
        period_end=date(2024, 3, 31),
        filed_at=datetime(2024, 5, 15, 16, 30, 0),
        is_amendment=False,
    )
    db_session.add(filing)
    db_session.flush()

    holding = Inst13fHolding(
        accession="0001067983-24-000001",
        cusip="037833100",
        ticker="AAPL",
        value_usd=150_000_000.0,
        shares=800_000.0,
        put_call=None,
    )
    db_session.add(holding)
    db_session.flush()

    cusip_entry = CusipMap(
        cusip="037833100",
        ticker="AAPL",
        source="openfigi",
    )
    db_session.add(cusip_entry)
    db_session.commit()

    # Query back and verify
    stored_filing = db_session.query(Inst13fFiling).filter_by(accession="0001067983-24-000001").first()
    assert stored_filing is not None
    assert stored_filing.cik == 1067983
    assert stored_filing.filer_name == "BERKSHIRE HATHAWAY INC"
    assert stored_filing.period_end == date(2024, 3, 31)
    assert stored_filing.filed_at == datetime(2024, 5, 15, 16, 30, 0)
    assert not stored_filing.is_amendment

    stored_holding = db_session.query(Inst13fHolding).filter_by(accession="0001067983-24-000001").first()
    assert stored_holding is not None
    assert stored_holding.cusip == "037833100"
    assert stored_holding.ticker == "AAPL"
    assert stored_holding.value_usd == 150_000_000.0

    stored_cusip = db_session.query(CusipMap).filter_by(cusip="037833100").first()
    assert stored_cusip is not None
    assert stored_cusip.ticker == "AAPL"
    assert stored_cusip.source == "openfigi"


def test_unique_accession_constraint_rejects_duplicate(db_session):
    """Verify that the unique constraint on accession rejects duplicate filings."""
    filing1 = Inst13fFiling(
        accession="0001067983-24-000001",
        cik=1067983,
        filer_name="BERKSHIRE HATHAWAY INC",
        period_end=date(2024, 3, 31),
        filed_at=datetime(2024, 5, 15, 16, 30, 0),
        is_amendment=False,
    )
    db_session.add(filing1)
    db_session.commit()

    filing2 = Inst13fFiling(
        accession="0001067983-24-000001",  # Same accession number
        cik=1067983,
        filer_name="BERKSHIRE HATHAWAY INC DUPLICATE",
        period_end=date(2024, 3, 31),
        filed_at=datetime(2024, 5, 15, 17, 0, 0),
        is_amendment=True,
    )
    db_session.add(filing2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_cusip_map_pk_rejects_duplicate(db_session):
    """Verify that cusip_map primary key rejects duplicate CUSIP entries."""
    entry1 = CusipMap(cusip="037833100", ticker="AAPL", source="openfigi")
    db_session.add(entry1)
    db_session.commit()

    entry2 = CusipMap(cusip="037833100", ticker="AAPL_DUP", source="manual")
    db_session.add(entry2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
