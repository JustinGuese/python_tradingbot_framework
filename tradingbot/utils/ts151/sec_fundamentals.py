"""SEC XBRL companyfacts fundamental data ingestion for TS151 strategy bots (PLAN-ts151.md S1).

Ingests point-in-time fundamentals from SEC EDGAR companyfacts API:
https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import text

from tradingbot.utils.buyback_scan import TokenBucket, fetch_edgar_bytes, load_sec_ticker_to_cik
from tradingbot.utils.db import SecFundamental, get_db_session
from tradingbot.utils.universes import SP100, SP500

logger = logging.getLogger(__name__)

# Forms to keep
ALLOWED_FORMS: frozenset[str] = frozenset({"10-K", "10-Q", "10-K/A", "10-Q/A"})

# Target tags by taxonomy
US_GAAP_TAGS: tuple[str, ...] = (
    "StockholdersEquity",
    "Assets",
    "Liabilities",
    "NetIncomeLoss",
    "EarningsPerShareDiluted",
    "CashAndCashEquivalentsAtCarryingValue",
    "GrossProfit",
    "OperatingIncomeLoss",
)
REVENUE_PRIMARY_TAG = "Revenues"
REVENUE_FALLBACK_TAG = "RevenueFromContractWithCustomerExcludingAssessedTax"

DEI_TAGS: tuple[str, ...] = (
    "EntityCommonStockSharesOutstanding",
)


def fetch_company_facts(
    cik: int,
    rate_limiter: TokenBucket | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Fetch raw XBRL company facts JSON for a given CIK from SEC EDGAR."""
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
    raw = fetch_edgar_bytes(url, rate_limiter=rate_limiter, timeout=timeout)
    return json.loads(raw.decode("utf-8"))


def parse_company_facts(
    data: dict[str, Any],
    symbol: str,
    cik: int,
) -> list[dict[str, Any]]:
    """Parse raw SEC company facts JSON into structured SecFundamental records.

    Extracts us-gaap core tags, revenue with ASC 606 fallback, and dei shares outstanding,
    filtering strictly for 10-K and 10-Q series filings.
    """
    facts_root = data.get("facts", {})
    us_gaap = facts_root.get("us-gaap", {})
    dei = facts_root.get("dei", {})

    records: list[dict[str, Any]] = []

    def _extract_concept_units(
        concept_data: dict[str, Any],
        tag_name: str,
    ) -> list[dict[str, Any]]:
        extracted: list[dict[str, Any]] = []
        units_map = concept_data.get("units", {})
        for unit_key, fact_items in units_map.items():
            for item in fact_items:
                form = str(item.get("form", "")).strip().upper()
                if form not in ALLOWED_FORMS:
                    continue

                end_str = item.get("end")
                filed_str = item.get("filed")
                val = item.get("val")

                if end_str is None or filed_str is None or val is None:
                    continue

                try:
                    period_end = pd.to_datetime(end_str).date()
                    filed = pd.to_datetime(filed_str).date()
                    value = float(val)
                except Exception:
                    continue

                fy = item.get("fy")
                fy_val = int(fy) if fy is not None else None
                fp = item.get("fp")
                fp_val = str(fp).strip() if fp is not None else None

                extracted.append(
                    {
                        "cik": cik,
                        "ticker": symbol,
                        "tag": tag_name,
                        "unit": str(unit_key),
                        "period_end": period_end,
                        "filed": filed,
                        "value": value,
                        "form": form,
                        "fy": fy_val,
                        "fp": fp_val,
                    }
                )
        return extracted

    # 1. Standard us-gaap tags
    for tag in US_GAAP_TAGS:
        if tag in us_gaap:
            records.extend(_extract_concept_units(us_gaap[tag], tag))

    # 2. Revenue tag with fallback
    # If primary Revenues exists, extract it
    revenue_records: list[dict[str, Any]] = []
    if REVENUE_PRIMARY_TAG in us_gaap:
        revenue_records.extend(_extract_concept_units(us_gaap[REVENUE_PRIMARY_TAG], REVENUE_PRIMARY_TAG))

    # If fallback concept exists, add missing (unit, period_end, filed, form) combinations under tag 'Revenues'
    if REVENUE_FALLBACK_TAG in us_gaap:
        fallback_records = _extract_concept_units(us_gaap[REVENUE_FALLBACK_TAG], REVENUE_PRIMARY_TAG)
        existing_keys = {
            (r["unit"], r["period_end"], r["filed"], r["form"])
            for r in revenue_records
        }
        for fb in fallback_records:
            fb_key = (fb["unit"], fb["period_end"], fb["filed"], fb["form"])
            if fb_key not in existing_keys:
                revenue_records.append(fb)
                existing_keys.add(fb_key)

    records.extend(revenue_records)

    # 3. DEI tags (shares outstanding)
    for tag in DEI_TAGS:
        if tag in dei:
            records.extend(_extract_concept_units(dei[tag], tag))

    return records


def upsert_sec_fundamentals(facts: Sequence[dict[str, Any]], batch_size: int = 1000) -> int:
    """Idempotently upsert fundamental records into ts151_sec_fundamentals.

    Returns the count of records processed.
    """
    if not facts:
        return 0

    total_upserted = 0
    with get_db_session() as session:
        dialect_name = session.bind.dialect.name if session.bind else "postgresql"

        for i in range(0, len(facts), batch_size):
            chunk = facts[i : i + batch_size]
            if dialect_name == "postgresql":
                from sqlalchemy.dialects.postgresql import insert as pg_insert

                stmt = pg_insert(SecFundamental).values(chunk)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["cik", "tag", "unit", "period_end", "filed", "form"],
                    set_={
                        "ticker": stmt.excluded.ticker,
                        "value": stmt.excluded.value,
                        "fy": stmt.excluded.fy,
                        "fp": stmt.excluded.fp,
                    },
                )
                session.execute(stmt)
            elif dialect_name == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert

                stmt = sqlite_insert(SecFundamental).values(chunk)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["cik", "tag", "unit", "period_end", "filed", "form"],
                    set_={
                        "ticker": stmt.excluded.ticker,
                        "value": stmt.excluded.value,
                        "fy": stmt.excluded.fy,
                        "fp": stmt.excluded.fp,
                    },
                )
                session.execute(stmt)
            else:
                for row in chunk:
                    existing = (
                        session.query(SecFundamental)
                        .filter_by(
                            cik=row["cik"],
                            tag=row["tag"],
                            unit=row["unit"],
                            period_end=row["period_end"],
                            filed=row["filed"],
                            form=row["form"],
                        )
                        .first()
                    )
                    if existing is not None:
                        existing.ticker = row["ticker"]
                        existing.value = row["value"]
                        existing.fy = row.get("fy")
                        existing.fp = row.get("fp")
                    else:
                        session.add(SecFundamental(**row))
            total_upserted += len(chunk)

    return total_upserted


def refresh_sec_fundamentals(
    tickers: Sequence[str] | None = None,
    rate_limiter: TokenBucket | None = None,
) -> tuple[int, int]:
    """Refresh SEC XBRL fundamentals for the target universe.

    Default universe is SP500 ∪ SP100.
    Returns (successful_tickers_count, failed_tickers_count).
    """
    if tickers is None:
        target_universe = sorted(set(SP500) | set(SP100))
    else:
        target_universe = list(tickers)

    logger.info("Starting SEC fundamentals refresh for %d tickers", len(target_universe))
    cik_map = load_sec_ticker_to_cik(rate_limiter=rate_limiter)

    success_count = 0
    failure_count = 0

    for symbol in target_universe:
        norm_sym = symbol.upper().strip()
        cik = cik_map.get(norm_sym)
        if cik is None:
            # Try dot vs dash substitution
            alt_sym = norm_sym.replace(".", "-") if "." in norm_sym else norm_sym.replace("-", ".")
            cik = cik_map.get(alt_sym)

        if cik is None:
            logger.warning("No CIK found for symbol %s in SEC mapping", symbol)
            failure_count += 1
            continue

        try:
            facts_data = fetch_company_facts(cik, rate_limiter=rate_limiter)
            parsed_facts = parse_company_facts(facts_data, symbol=norm_sym, cik=cik)
            if parsed_facts:
                upsert_sec_fundamentals(parsed_facts)
            success_count += 1
            logger.info("Successfully refreshed %s (CIK %d): %d facts", symbol, cik, len(parsed_facts))
        except Exception as exc:
            logger.error("Failed to fetch or parse SEC fundamentals for %s (CIK %d): %s", symbol, cik, exc)
            failure_count += 1

    logger.info(
        "Completed SEC fundamentals refresh: %d succeeded, %d failed",
        success_count,
        failure_count,
    )
    return success_count, failure_count
