"""
Regression tests for interval scoping in the historic_data table.

The bug these pin: HistoricData's primary key used to be (symbol, timestamp)
with no interval, so a symbol cached at 1m and a symbol cached at 1d shared one
pile of rows. xauzenbot writes ^XAU 1-minute bars every 5 minutes, so asking for
interval="1d" handed back 74k one-minute rows and every TA indicator computed on
them was meaningless — silently, with no error.
"""

from contextlib import contextmanager
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tradingbot.utils.db import Base, HistoricData
from tradingbot.utils.historic_repository import HistoricDataRepository

TS = datetime(2026, 8, 10, 0, 0, 0)


@pytest.fixture
def repo(monkeypatch):
    """A HistoricDataRepository backed by a throwaway in-memory SQLite DB."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    @contextmanager
    def _session():
        session = Session()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    monkeypatch.setattr("tradingbot.utils.historic_repository.get_db_session", _session)

    def _add(symbol, interval, timestamp, close):
        with _session() as s:
            s.add(
                HistoricData(
                    symbol=symbol,
                    interval=interval,
                    timestamp=timestamp,
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=1.0,
                )
            )

    r = HistoricDataRepository()
    r._add = _add  # type: ignore[attr-defined]
    return r


def test_same_timestamp_can_hold_both_a_1m_and_a_1d_bar(repo):
    """
    The widened primary key is the actual fix — under the old (symbol, timestamp)
    key these two rows were the same row, so one silently displaced the other.
    """
    repo._add("^XAU", "1m", TS, 100.0)
    repo._add("^XAU", "1d", TS, 200.0)

    assert repo.get_range("^XAU", "1m")["close"].tolist() == [100.0]
    assert repo.get_range("^XAU", "1d")["close"].tolist() == [200.0]


def test_get_range_never_returns_another_interval(repo):
    for minute in range(5):
        repo._add("^XAU", "1m", datetime(2026, 8, 10, 14, minute), 100.0 + minute)
    repo._add("^XAU", "1d", TS, 999.0)

    daily = repo.get_range("^XAU", "1d")
    assert len(daily) == 1
    assert daily["close"].tolist() == [999.0]
    assert len(repo.get_range("^XAU", "1m")) == 5


def test_latest_timestamp_is_scoped_to_the_interval(repo):
    """
    This is what made ^XAU accumulate zero daily rows: add_pd_df_to_db only
    inserts bars newer than the high-water mark, and unscoped that mark was the
    newest 1-minute bar — minutes old, so every daily bar looked stale.
    """
    repo._add("^XAU", "1d", datetime(2026, 8, 9), 100.0)
    repo._add("^XAU", "1m", datetime(2026, 8, 10, 20, 59), 101.0)

    assert repo.get_latest_timestamp("^XAU", "1d") == datetime(2026, 8, 9)
    assert repo.get_latest_timestamp("^XAU", "1m") == datetime(2026, 8, 10, 20, 59)


def test_missing_interval_is_rejected_rather_than_defaulted(repo):
    with pytest.raises(ValueError, match="interval"):
        repo.get_range("^XAU", "")
    with pytest.raises(ValueError, match="interval"):
        repo.get_latest_timestamp("^XAU", "")


def test_bulk_insert_rejects_rows_without_an_interval(repo):
    """An unlabelled row would land under whatever the column defaults to."""
    with pytest.raises(ValueError, match="interval"):
        repo.bulk_insert_ohlcv(
            [{"symbol": "^XAU", "timestamp": TS, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}]
        )
