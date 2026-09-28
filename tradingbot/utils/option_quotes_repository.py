"""Bulk-insert path for the `option_quotes` table.

Separate from utils/options.py's `fetch_option_chain`, which `session.add_all`s
a single live fetch where the fresh `snapshot_at` can never collide with an
existing row. A historical backfill (utils/dolthub_options.py) re-runs over the
same date range on every resume, so it needs ON CONFLICT DO NOTHING on
(contract_symbol, snapshot_at) — the same pattern HistoricDataRepository uses
for `historic_data`.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.dialects.postgresql import insert

from .db import OptionQuote, get_db_session

_REQUIRED_KEYS = ("underlying", "contract_symbol", "expiration", "option_type", "strike", "snapshot_at")


def bulk_insert_quotes(rows: Iterable[dict]) -> int:
    """Bulk insert OptionQuote rows using ON CONFLICT (contract_symbol, snapshot_at) DO NOTHING.

    Returns the number of rows actually inserted (duplicates against an earlier
    run are silently skipped, not counted).
    """
    rows = list(rows)
    if not rows:
        return 0

    missing = [k for k in _REQUIRED_KEYS if k not in rows[0]]
    if missing:
        raise ValueError(f"Option quote rows are missing required key(s): {missing}")

    stmt = (
        insert(OptionQuote)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["contract_symbol", "snapshot_at"])
        .returning(OptionQuote.id)
    )
    with get_db_session() as session:
        result = session.execute(stmt)
        return len(result.fetchall())
