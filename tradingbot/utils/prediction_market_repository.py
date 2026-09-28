"""Bulk-insert path for `prediction_market_snapshots`.

ON CONFLICT (venue, market_ticker, date) DO NOTHING, like
option_quotes_repository: the daily job re-reads the last few days and the
backfill re-runs whole histories, and neither may duplicate a row. Built with the
session's own dialect so the same path runs on SQLite in tests.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.dialects import postgresql, sqlite

from .db import PredictionMarketSnapshot, get_db_session

_REQUIRED_KEYS = ("date", "observed_at", "venue", "series", "market_ticker", "contract_type")
_CHUNK = 2000
_CONFLICT = ["venue", "market_ticker", "date"]


def bulk_insert_snapshots(rows: Iterable[dict]) -> int:
    """Insert rows, skipping any (venue, market_ticker, date) already stored. Returns rows inserted."""
    rows = list(rows)
    if not rows:
        return 0
    missing = [k for k in _REQUIRED_KEYS if k not in rows[0]]
    if missing:
        raise ValueError(f"Prediction market rows are missing required key(s): {missing}")

    inserted = 0
    with get_db_session() as session:
        dialect = session.get_bind().dialect.name
        insert = sqlite.insert if dialect == "sqlite" else postgresql.insert
        for i in range(0, len(rows), _CHUNK):
            stmt = (
                insert(PredictionMarketSnapshot)
                .values(rows[i : i + _CHUNK])
                .on_conflict_do_nothing(index_elements=_CONFLICT)
                .returning(PredictionMarketSnapshot.id)
            )
            inserted += len(session.execute(stmt).fetchall())
    return inserted


def record_results(venue: str, results: dict[str, str]) -> int:
    """Stamp settled markets' result ("yes"/"no") onto rows still missing it. Returns rows updated."""
    if not results:
        return 0
    updated = 0
    with get_db_session() as session:
        pending = {
            ticker
            for (ticker,) in session.query(PredictionMarketSnapshot.market_ticker)
            .filter(PredictionMarketSnapshot.venue == venue, PredictionMarketSnapshot.result.is_(None))
            .distinct()
        }
        for ticker in pending & results.keys():
            result = results[ticker]
            updated += (
                session.query(PredictionMarketSnapshot)
                .filter(
                    PredictionMarketSnapshot.venue == venue,
                    PredictionMarketSnapshot.market_ticker == ticker,
                    PredictionMarketSnapshot.result.is_(None),
                )
                .update({"result": result}, synchronize_session=False)
            )
    return updated
