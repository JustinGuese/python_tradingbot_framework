"""CFTC Commitments of Traders (COT) legacy futures ingestion (PLAN-ts151.md S2).

Ingests legacy futures positioning data from the CFTC Public Reporting API:
https://publicreporting.cftc.gov/resource/6dca-aqww.json
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

import pandas as pd

from tradingbot.utils.db import CotPosition, get_db_session
from tradingbot.utils.ts151.universes import COT_CODES

logger = logging.getLogger(__name__)

CFTC_COT_ENDPOINT = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"

# Verification keywords required in market_and_exchange_names per contract market code
EXPECTED_COMMODITY_WORDS: dict[str, str] = {
    "067651": "CRUDE",
    "023651": "NATURAL GAS",
    "088691": "GOLD",
    "084691": "SILVER",
    "085692": "COPPER",
    "002602": "CORN",
    "001602": "WHEAT",
    "005602": "SOYBEANS",
    "080732": "SUGAR",
}


def verify_cot_market_names(records: Sequence[dict[str, Any]]) -> None:
    """Verify that each code's market_name contains the expected commodity keyword.

    Raises ValueError on any mismatch.
    """
    for r in records:
        code = str(r.get("market_code", ""))
        expected_word = EXPECTED_COMMODITY_WORDS.get(code)
        if expected_word:
            mname = str(r.get("market_name", "")).upper()
            if expected_word not in mname:
                raise ValueError(f"CFTC market name mismatch for code {code}: expected '{expected_word}' in '{mname}'")


def fetch_cot_records(
    market_codes: Sequence[str],
    start_date: date | None = None,
    endpoint: str = CFTC_COT_ENDPOINT,
    timeout: float = 30.0,
    page_size: int = 2000,
) -> list[dict[str, Any]]:
    """Fetch and parse COT records from the CFTC Public Reporting SODA API with pagination."""
    if not market_codes:
        return []

    codes_formatted = ", ".join(f"'{c}'" for c in market_codes)
    where_clause = f"cftc_contract_market_code in ({codes_formatted})"
    if start_date is not None:
        where_clause += f" and report_date_as_yyyy_mm_dd >= '{start_date.isoformat()}'"

    records: list[dict[str, Any]] = []
    offset = 0

    while True:
        params = {
            "$where": where_clause,
            "$order": "report_date_as_yyyy_mm_dd ASC, cftc_contract_market_code ASC",
            "$limit": str(page_size),
            "$offset": str(offset),
        }
        query_str = urllib.parse.urlencode(params)
        url = f"{endpoint}?{query_str}"

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "tradingbot-research",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            logger.error("Failed to fetch CFTC COT data from %s: %s", url, exc)
            raise

        if not isinstance(data, list) or not data:
            break

        for item in data:
            code = item.get("cftc_contract_market_code")
            dt_str = item.get("report_date_as_yyyy_mm_dd")
            if not code or not dt_str:
                continue

            try:
                rep_date = pd.to_datetime(dt_str).date()
                rec = {
                    "market_code": str(code),
                    "report_date": rep_date,
                    "market_name": str(item.get("market_and_exchange_names", "")),
                    "open_interest": float(item.get("open_interest_all", 0.0)),
                    "comm_long": float(item.get("comm_positions_long_all", 0.0)),
                    "comm_short": float(item.get("comm_positions_short_all", 0.0)),
                    "noncomm_long": float(item.get("noncomm_positions_long_all", 0.0)),
                    "noncomm_short": float(item.get("noncomm_positions_short_all", 0.0)),
                    "nonrep_long": float(item.get("nonrept_positions_long_all", 0.0)),
                    "nonrep_short": float(item.get("nonrept_positions_short_all", 0.0)),
                }
                records.append(rec)
            except Exception as e:
                logger.warning("Error parsing CFTC COT row %s: %s", item, e)
                continue

        if len(data) < page_size:
            break
        offset += page_size

    return records


def upsert_cot_positions(records: Sequence[dict[str, Any]], batch_size: int = 1000) -> int:
    """Idempotently upsert COT position records into ts151_cot_positions.

    Returns the count of records processed.
    """
    if not records:
        return 0

    total_upserted = 0
    with get_db_session() as session:
        dialect_name = session.bind.dialect.name if session.bind else "postgresql"

        for i in range(0, len(records), batch_size):
            chunk = records[i : i + batch_size]
            if dialect_name == "postgresql":
                from sqlalchemy.dialects.postgresql import insert as pg_insert

                stmt = pg_insert(CotPosition).values(chunk)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["market_code", "report_date"],
                    set_={
                        "market_name": stmt.excluded.market_name,
                        "open_interest": stmt.excluded.open_interest,
                        "comm_long": stmt.excluded.comm_long,
                        "comm_short": stmt.excluded.comm_short,
                        "noncomm_long": stmt.excluded.noncomm_long,
                        "noncomm_short": stmt.excluded.noncomm_short,
                        "nonrep_long": stmt.excluded.nonrep_long,
                        "nonrep_short": stmt.excluded.nonrep_short,
                    },
                )
                session.execute(stmt)
            elif dialect_name == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert

                stmt = sqlite_insert(CotPosition).values(chunk)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["market_code", "report_date"],
                    set_={
                        "market_name": stmt.excluded.market_name,
                        "open_interest": stmt.excluded.open_interest,
                        "comm_long": stmt.excluded.comm_long,
                        "comm_short": stmt.excluded.comm_short,
                        "noncomm_long": stmt.excluded.noncomm_long,
                        "noncomm_short": stmt.excluded.noncomm_short,
                        "nonrep_long": stmt.excluded.nonrep_long,
                        "nonrep_short": stmt.excluded.nonrep_short,
                    },
                )
                session.execute(stmt)
            else:
                for row in chunk:
                    existing = (
                        session.query(CotPosition)
                        .filter_by(
                            market_code=row["market_code"],
                            report_date=row["report_date"],
                        )
                        .first()
                    )
                    if existing is not None:
                        existing.market_name = row["market_name"]
                        existing.open_interest = row["open_interest"]
                        existing.comm_long = row["comm_long"]
                        existing.comm_short = row["comm_short"]
                        existing.noncomm_long = row["noncomm_long"]
                        existing.noncomm_short = row["noncomm_short"]
                        existing.nonrep_long = row["nonrep_long"]
                        existing.nonrep_short = row["nonrep_short"]
                    else:
                        session.add(CotPosition(**row))
            total_upserted += len(chunk)

    return total_upserted


def refresh_cot(
    codes: Sequence[str] | None = None,
    backfill: bool = False,
    days: int = 30,
    endpoint: str = CFTC_COT_ENDPOINT,
) -> int:
    """Refresh CFTC COT positions.

    If backfill is True, or if the table has no rows for any code, fetches full history
    back to 1986 (auto-backfill). Otherwise fetches the last `days` days.
    """
    target_codes = list(codes) if codes is not None else list(COT_CODES.values())

    # Check if any code has zero rows in the DB
    needs_backfill = backfill
    if not needs_backfill:
        with get_db_session() as session:
            for code in target_codes:
                count = session.query(CotPosition).filter_by(market_code=code).count()
                if count == 0:
                    logger.info("Market code %s has no existing rows; triggering auto-backfill from 1986", code)
                    needs_backfill = True
                    break

    start_date: date | None = None
    if needs_backfill:
        start_date = date(1986, 1, 1)
        logger.info("Running COT backfill from %s for codes: %s", start_date, target_codes)
    else:
        start_date = date.today() - timedelta(days=days)
        logger.info("Running incremental COT update from %s for codes: %s", start_date, target_codes)

    records = fetch_cot_records(target_codes, start_date=start_date, endpoint=endpoint)
    logger.info("Fetched %d CFTC COT records", len(records))

    if records:
        verify_cot_market_names(records)
        upserted = upsert_cot_positions(records)
        logger.info("Successfully upserted %d COT position records", upserted)
        return upserted

    return 0
