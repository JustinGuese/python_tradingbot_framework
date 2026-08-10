"""Repository for historic OHLCV data stored in the database.

This module centralizes all direct database access for the `historic_data`
table so that higher-level services (like `DataService`) can remain focused on
data fetching, merging, and cleaning logic.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
from sqlalchemy.dialects.postgresql import insert

from .db import HistoricData, get_db_session


@dataclass
class HistoricDataRepository:
    """Repository abstraction for the `historic_data` table.

    Responsibilities:
    - Query historic OHLCV rows for a symbol or range.
    - Fetch the latest timestamp for a given symbol.
    - Perform bulk inserts with proper duplicate handling.
    """

    def get_latest_timestamp(self, symbol: str, interval: str) -> datetime | None:
        """
        Return the latest timestamp stored for a (symbol, interval), or None.

        `interval` is required: scoped to the symbol alone this returns the newest
        bar of ANY size, so for a symbol cached at 1m the answer is always minutes
        old and a daily writer concludes it has nothing new to insert.
        """
        if not symbol:
            raise ValueError("symbol must be a non-empty string")
        if not interval:
            raise ValueError("interval must be a non-empty string")

        with get_db_session() as session:
            latest = (
                session.query(HistoricData.timestamp)
                .filter_by(symbol=symbol, interval=interval)
                .order_by(HistoricData.timestamp.desc())
                .first()
            )
            return latest[0] if latest else None

    def get_range(
        self,
        symbol: str,
        interval: str,
        start_date: pd.Timestamp | None = None,
        end_date: pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        """
        Load historic data for a (symbol, interval) in an optional date range.

        `interval` is required — see HistoricData's docstring for what happens
        when bar sizes are allowed to mix.
        """
        if not symbol:
            raise ValueError("symbol must be a non-empty string")
        if not interval:
            raise ValueError("interval must be a non-empty string")

        with get_db_session() as session:
            query = session.query(HistoricData).filter_by(symbol=symbol, interval=interval)

            if start_date is not None:
                query = query.filter(HistoricData.timestamp >= start_date)
            if end_date is not None:
                query = query.filter(HistoricData.timestamp <= end_date)

            query = query.order_by(HistoricData.timestamp)
            results = query.all()

            if not results:
                return pd.DataFrame()

            # Build row dicts while session is open to avoid DetachedInstanceError
            # `interval` is deliberately NOT projected: the caller already chose it,
            # and an extra object-dtype column here would flow through
            # _merge_db_and_yf (where yfinance rows have no such value) into
            # add_all_ta_features.
            rows = [
                {
                    "symbol": r.symbol,
                    "timestamp": r.timestamp,
                    "open": r.open,
                    "high": r.high,
                    "low": r.low,
                    "close": r.close,
                    "volume": r.volume,
                }
                for r in results
            ]

        return pd.DataFrame(rows)

    def bulk_insert_ohlcv(self, rows: Iterable[dict]) -> None:
        """Bulk insert OHLCV rows using ON CONFLICT DO NOTHING semantics.

        Each row must contain keys:
        - symbol
        - interval
        - timestamp
        - open, high, low, close, volume
        """
        rows = list(rows)
        if not rows:
            return

        missing = [k for k in ("symbol", "interval", "timestamp") if k not in rows[0]]
        if missing:
            raise ValueError(f"OHLCV rows are missing required key(s): {missing}")

        stmt = (
            insert(HistoricData).values(rows).on_conflict_do_nothing(index_elements=["symbol", "interval", "timestamp"])
        )
        with get_db_session() as session:
            session.execute(stmt)
