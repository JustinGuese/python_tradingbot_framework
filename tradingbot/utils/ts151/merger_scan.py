"""EDGAR merger and tender offer filing scanner for TS151 strategy bots (PLAN-ts151.md S3).

Scans SEC EDGAR daily and quarterly form indices for merger / tender offer announcements:
- SC TO-T, SC TO-T/A: Tender offer by third party (extract target from SUBJECT-COMPANY block)
- SC 14D9, SC 14D9/A: Recommendation/response statement (extract target from SUBJECT-COMPANY block)
- DEFM14A, PREM14A: Merger proxy statements (filer is the target)
"""

from __future__ import annotations

import logging
import re
import urllib.error
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pandas as pd

from tradingbot.utils.buyback_scan import (
    TokenBucket,
    fetch_edgar_bytes,
    load_sec_ticker_to_cik,
    parse_acceptance_datetime,
)
from tradingbot.utils.db import MergerEvent, get_db_session

logger = logging.getLogger(__name__)

TARGET_FORMS: frozenset[str] = frozenset({"SC TO-T", "SC 14D9", "DEFM14A", "PREM14A"})
SUBJECT_COMPANY_FORMS: frozenset[str] = frozenset({"SC TO-T", "SC 14D9"})

_CIK_TO_TICKER_CACHE: dict[int, str] = {}


def load_sec_cik_to_ticker(rate_limiter: TokenBucket | None = None) -> dict[int, str]:
    """Load reverse CIK-to-ticker mapping from SEC EDGAR."""
    if _CIK_TO_TICKER_CACHE:
        return _CIK_TO_TICKER_CACHE

    t_to_c = load_sec_ticker_to_cik(rate_limiter=rate_limiter)
    mapping = {cik: ticker for ticker, cik in t_to_c.items()}
    _CIK_TO_TICKER_CACHE.update(mapping)
    return _CIK_TO_TICKER_CACHE


def clear_cik_to_ticker_cache() -> None:
    """Clear in-memory CIK to ticker cache (for testing)."""
    _CIK_TO_TICKER_CACHE.clear()


def parse_subject_company_from_header(header_text: str) -> tuple[int, str] | None:
    """Extract (subject_cik, subject_company_name) from SEC submission header text or HTML.

    Searches specifically within the SUBJECT COMPANY / SUBJECT-COMPANY section.
    """
    # 1. Isolate the subject company block
    match_block = re.search(
        r"(?:<SUBJECT-COMPANY>|SUBJECT COMPANY:)(.*?)(?:</SUBJECT-COMPANY>|<FILED-BY>|FILED BY:|<FILER>|FILER:|$)",
        header_text,
        re.DOTALL | re.IGNORECASE,
    )
    if not match_block:
        return None

    subj_text = match_block.group(1)

    # 2. Extract CIK
    cik_match = re.search(
        r"(?:<CIK>|CENTRAL INDEX KEY:\s*)([0-9]+)",
        subj_text,
        re.IGNORECASE,
    )
    if not cik_match:
        return None
    cik = int(cik_match.group(1))

    # 3. Extract Company Name
    name_match = re.search(
        r"(?:<CONFORMED-NAME>|COMPANY CONFORMED NAME:\s*)([^\r\n<]+)",
        subj_text,
        re.IGNORECASE,
    )
    company_name = name_match.group(1).strip() if name_match else ""

    return cik, company_name


def parse_header_acceptance_datetime(header_text: str) -> datetime | None:
    """Extract acceptance datetime from filing header if present."""
    match = re.search(
        r"(?:<ACCEPTANCE-DATETIME>|ACCEPTANCE-DATETIME:\s*)([0-9]{14})",
        header_text,
        re.IGNORECASE,
    )
    if match:
        dt_str = match.group(1)
        try:
            return parse_acceptance_datetime(dt_str)
        except Exception:
            pass
    return None


def parse_form_index_lines(content: str) -> list[dict[str, Any]]:
    """Parse lines of an EDGAR form.idx file into candidate items.

    Returns dicts with form, company_name, cik, filing_date, file_name, accession.
    """
    lines = content.splitlines()
    data_lines_started = False
    candidates: list[dict[str, Any]] = []

    for line in lines:
        if not data_lines_started:
            if line.startswith("---") or line.startswith("==="):
                data_lines_started = True
            continue

        if len(line) < 99:
            continue

        form_type = line[0:12].strip()
        base_form = form_type.split("/")[0].strip().upper()
        if base_form not in TARGET_FORMS and form_type.upper() not in TARGET_FORMS:
            continue

        company_name = line[12:74].strip()
        cik_str = line[74:86].strip()
        date_str = line[86:98].strip()
        file_name = line[98:].strip()

        try:
            cik = int(cik_str)
        except ValueError:
            continue

        # Extract accession from file_name (e.g. edgar/data/1774474/0001193125-24-002883.txt)
        acc_match = re.search(r"([0-9]{10}-[0-9]{2}-[0-9]{6})", file_name)
        if not acc_match:
            continue
        accession = acc_match.group(1)

        candidates.append(
            {
                "form": form_type,
                "base_form": base_form,
                "company_name": company_name,
                "cik": cik,
                "filing_date": date_str,
                "file_name": file_name,
                "accession": accession,
            }
        )

    return candidates


def resolve_filing_target(
    cand: dict[str, Any],
    rate_limiter: TokenBucket | None = None,
) -> dict[str, Any] | None:
    """Resolve target CIK, company_name, and filed_at timestamp for a candidate filing."""
    base_form = cand["base_form"]
    accession = cand["accession"]
    filing_date_str = cand["filing_date"]

    if base_form in SUBJECT_COMPANY_FORMS:
        # Fetch filing header to parse SUBJECT-COMPANY CIK
        cik = cand["cik"]
        acc_no_dashes = accession.replace("-", "")
        header_url = (
            f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no_dashes}/"
            f"{accession}-index-headers.html"
        )
        try:
            header_bytes = fetch_edgar_bytes(header_url, rate_limiter=rate_limiter, max_retries=1)
            header_text = header_bytes.decode("latin1")
        except Exception:
            # Fallback to .txt full submission header
            txt_url = f"https://www.sec.gov/Archives/{cand['file_name']}"
            try:
                txt_bytes = fetch_edgar_bytes(txt_url, rate_limiter=rate_limiter, max_retries=1)
                header_text = txt_bytes[:65536].decode("latin1")
            except Exception as e:
                logger.warning("Failed to fetch header for accession %s: %s", accession, e)
                return None

        parsed_subj = parse_subject_company_from_header(header_text)
        if not parsed_subj:
            logger.warning("Could not extract SUBJECT-COMPANY from header for accession %s", accession)
            return None

        target_cik, target_name = parsed_subj
        filed_at = parse_header_acceptance_datetime(header_text)
        if filed_at is None:
            filed_at = parse_acceptance_datetime(None, filing_date_str=filing_date_str)

        return {
            "accession": accession,
            "cik": target_cik,
            "company_name": target_name or cand["company_name"],
            "form": cand["form"],
            "filed_at": filed_at,
        }
    else:
        # For DEFM14A and PREM14A, filer is the target
        filed_at = parse_acceptance_datetime(None, filing_date_str=filing_date_str)
        return {
            "accession": accession,
            "cik": cand["cik"],
            "company_name": cand["company_name"],
            "form": cand["form"],
            "filed_at": filed_at,
        }


def upsert_merger_events(events: Sequence[dict[str, Any]], batch_size: int = 500) -> int:
    """Idempotently insert merger events into ts151_merger_events on accession."""
    if not events:
        return 0

    total = 0
    with get_db_session() as session:
        dialect_name = session.bind.dialect.name if session.bind else "postgresql"

        for i in range(0, len(events), batch_size):
            chunk = events[i : i + batch_size]
            if dialect_name == "postgresql":
                from sqlalchemy.dialects.postgresql import insert as pg_insert

                stmt = pg_insert(MergerEvent).values(chunk)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["accession"],
                    set_={
                        "cik": stmt.excluded.cik,
                        "ticker": stmt.excluded.ticker,
                        "company_name": stmt.excluded.company_name,
                        "form": stmt.excluded.form,
                        "filed_at": stmt.excluded.filed_at,
                    },
                )
                session.execute(stmt)
            elif dialect_name == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert

                stmt = sqlite_insert(MergerEvent).values(chunk)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["accession"],
                    set_={
                        "cik": stmt.excluded.cik,
                        "ticker": stmt.excluded.ticker,
                        "company_name": stmt.excluded.company_name,
                        "form": stmt.excluded.form,
                        "filed_at": stmt.excluded.filed_at,
                    },
                )
                session.execute(stmt)
            else:
                for ev in chunk:
                    existing = session.query(MergerEvent).filter_by(accession=ev["accession"]).first()
                    if existing is not None:
                        existing.cik = ev["cik"]
                        existing.ticker = ev.get("ticker")
                        existing.company_name = ev["company_name"]
                        existing.form = ev["form"]
                        existing.filed_at = ev["filed_at"]
                    else:
                        session.add(MergerEvent(**ev))
            total += len(chunk)

    return total


def touch_heartbeat(now: datetime | None = None) -> None:
    """Record heartbeat row in ts151_merger_events so a quiet market is not mistaken for a dead scanner."""
    stamp = (now or datetime.now(UTC)).replace(tzinfo=None)
    with get_db_session() as session:
        dialect_name = session.bind.dialect.name if session.bind else "postgresql"
        data = {
            "accession": "_HEARTBEAT",
            "cik": 0,
            "ticker": None,
            "company_name": "_HEARTBEAT",
            "form": "_HEARTBEAT",
            "filed_at": stamp,
            "created_at": stamp,
        }
        if dialect_name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            stmt = pg_insert(MergerEvent).values(data)
            stmt = stmt.on_conflict_do_update(
                index_elements=["accession"],
                set_={"filed_at": stamp, "created_at": stamp},
            )
            session.execute(stmt)
        elif dialect_name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            stmt = sqlite_insert(MergerEvent).values(data)
            stmt = stmt.on_conflict_do_update(
                index_elements=["accession"],
                set_={"filed_at": stamp, "created_at": stamp},
            )
            session.execute(stmt)
        else:
            row = session.query(MergerEvent).filter_by(accession="_HEARTBEAT").first()
            if row is None:
                session.add(MergerEvent(**data))
            else:
                row.filed_at = stamp
                row.created_at = stamp


def scan_daily_form_index(
    target_date: date,
    rate_limiter: TokenBucket | None = None,
    cik_to_ticker: dict[int, str] | None = None,
) -> list[dict[str, Any]]:
    """Scan EDGAR daily form index for a single date."""
    year = target_date.year
    quarter = (target_date.month - 1) // 3 + 1
    date_str = target_date.strftime("%Y%m%d")
    url = f"https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{quarter}/form.{date_str}.idx"

    try:
        raw_bytes = fetch_edgar_bytes(url, rate_limiter=rate_limiter)
    except urllib.error.HTTPError as he:
        if he.code == 404:
            logger.info("Form index not found for %s (market closed/holiday): 404", date_str)
            return []
        raise
    except Exception as e:
        logger.error("Failed to fetch daily form index %s: %s", url, e)
        raise

    content = raw_bytes.decode("latin1")
    candidates = parse_form_index_lines(content)
    if not candidates:
        return []

    # Deduplicate candidates by accession (e.g. SC TO-T listed under filer and subject)
    deduped_candidates: dict[str, dict[str, Any]] = {}
    for c in candidates:
        acc = c["accession"]
        if acc not in deduped_candidates:
            deduped_candidates[acc] = c

    resolved_events: list[dict[str, Any]] = []
    mapping = cik_to_ticker if cik_to_ticker is not None else load_sec_cik_to_ticker(rate_limiter=rate_limiter)

    for c in deduped_candidates.values():
        res = resolve_filing_target(c, rate_limiter=rate_limiter)
        if res:
            res["ticker"] = mapping.get(res["cik"])
            resolved_events.append(res)

    return resolved_events


def scan_mergers(
    backfill_year: int | None = None,
    days: int = 5,
    rate_limiter: TokenBucket | None = None,
) -> int:
    """Scan merger announcements and update ts151_merger_events.

    Defaults to scanning the last `days` business days.
    If backfill_year is specified or table has no non-heartbeat rows, auto-backfills.
    """
    with get_db_session() as session:
        real_events_count = (
            session.query(MergerEvent)
            .filter(MergerEvent.accession != "_HEARTBEAT")
            .count()
        )

    start_year = backfill_year
    if start_year is None and real_events_count == 0:
        logger.info("ts151_merger_events has no existing events; triggering auto-backfill from 2005")
        start_year = 2005

    mapping = load_sec_cik_to_ticker(rate_limiter=rate_limiter)
    total_upserted = 0

    if start_year is not None:
        current_year = date.today().year
        logger.info("Running merger scan backfill from year %d to %d", start_year, current_year)
        for yr in range(start_year, current_year + 1):
            for qtr in range(1, 5):
                qtr_url = f"https://www.sec.gov/Archives/edgar/full-index/{yr}/QTR{qtr}/form.idx"
                try:
                    raw_bytes = fetch_edgar_bytes(qtr_url, rate_limiter=rate_limiter)
                except urllib.error.HTTPError as he:
                    if he.code == 404:
                        continue
                    raise
                except Exception as e:
                    logger.warning("Could not fetch quarterly form index %s: %s", qtr_url, e)
                    continue

                content = raw_bytes.decode("latin1")
                candidates = parse_form_index_lines(content)
                deduped: dict[str, dict[str, Any]] = {}
                for c in candidates:
                    acc = c["accession"]
                    if acc not in deduped:
                        deduped[acc] = c

                events: list[dict[str, Any]] = []
                for c in deduped.values():
                    res = resolve_filing_target(c, rate_limiter=rate_limiter)
                    if res:
                        res["ticker"] = mapping.get(res["cik"])
                        events.append(res)

                if events:
                    upserted = upsert_merger_events(events)
                    total_upserted += upserted
                    logger.info("Year %d QTR%d: upserted %d merger events", yr, qtr, upserted)
    else:
        # Incremental scan: last `days` days
        today = date.today()
        dates_to_scan = [today - timedelta(days=d) for d in range(days)]
        for d in reversed(dates_to_scan):
            # Skip weekends
            if d.weekday() >= 5:
                continue
            try:
                events = scan_daily_form_index(d, rate_limiter=rate_limiter, cik_to_ticker=mapping)
                if events:
                    upserted = upsert_merger_events(events)
                    total_upserted += upserted
                    logger.info("Date %s: upserted %d merger events", d, upserted)
            except Exception as exc:
                if d == today:
                    logger.error("Scan failed on today's index (%s): %s", d, exc)
                    raise
                logger.warning("Scan failed for date %s: %s", d, exc)

    # Touch heartbeat row on successful completion
    touch_heartbeat()
    logger.info("Merger scan completed. Total events upserted: %d. Heartbeat touched.", total_upserted)
    return total_upserted
