"""
SEC EDGAR Schedule 13D scanner.

For each name in the activist universe, reads the company's EDGAR submissions JSON (13D filings made against a
company are listed there), keeps initial SC 13D / SCHEDULE 13D filings since a cutoff, fetches the primary document
of any not yet stored, classifies the filer (utils/sc13d_rules.py) and inserts one `sc13d_events` row per accession.

Reuses the rate limiter, fetch and CIK helpers of utils/buyback_scan.py (5 requests per second, generic User-Agent).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError

from tradingbot.utils.buyback_rules import clean_html
from tradingbot.utils.buyback_scan import (
    DEFAULT_RATE_LIMITER,
    MAX_FAILURE_RATIO,
    TokenBucket,
    fetch_edgar_bytes,
    load_sec_ticker_to_cik,
    parse_acceptance_datetime,
)
from tradingbot.utils.db import Sc13dEvent, get_db_session
from tradingbot.utils.sc13d_rules import is_fund_like, is_initial_13d, reporting_names
from tradingbot.utils.universes import ACTIVIST_UNIVERSE

logger = logging.getLogger(__name__)

# The bot holds a name for 20 sessions (~28 days), so 45 days of history is a superset of what can be active. The first
# run therefore seeds the whole live book by itself; later runs only find what is new (insertion is idempotent).
DEFAULT_SINCE_DAYS = 45
HEARTBEAT_ACCESSION = "heartbeat"
FILERS_MAX_CHARS = 300
# The current 13D format puts the cover page ~7,000+ characters into the document, behind a long SEC preamble
# (a 6,000 character window found no filer name in any of the 11 filings of the 2026-11 smoke test).
COVER_SEARCH_CHARS = 120_000
SCAN_WORKERS = 6  # requests are latency-bound; the shared token bucket still caps the rate at 5/s


def parse_13d_filings(
    submissions_data: dict[str, Any],
    symbol: str,
    cik: int,
    cutoff_dt: datetime,
    rate_limiter: TokenBucket | None = None,
) -> list[dict[str, Any]]:
    """Initial 13D filings accepted since ``cutoff_dt``, from one company's submissions JSON."""
    out: list[dict[str, Any]] = []
    cutoff_date = cutoff_dt.date().isoformat()

    def _group(g: dict[str, Any]) -> None:
        forms = g.get("form", [])
        for i, form in enumerate(forms):
            if not is_initial_13d(form):
                continue
            fdate = str(g["filingDate"][i])
            if fdate < cutoff_date:
                continue
            pdoc = str(g["primaryDocument"][i]) if i < len(g.get("primaryDocument", [])) else ""
            if not pdoc:
                continue
            acc_dt = str(g["acceptanceDateTime"][i]) if i < len(g.get("acceptanceDateTime", [])) else None
            try:
                filed_at = parse_acceptance_datetime(acc_dt, fdate)
            except Exception:
                continue
            if filed_at < cutoff_dt:
                continue
            out.append(
                {
                    "symbol": symbol,
                    "cik": cik,
                    "accession": str(g["accessionNumber"][i]),
                    "filed_at": filed_at,
                    "primary_doc": pdoc,
                }
            )

    recent = submissions_data.get("filings", {}).get("recent", {})
    if recent:
        _group(recent)
    older = submissions_data.get("filings", {}).get("files", [])
    recent_dates = recent.get("filingDate", [])
    if older and recent_dates and min(recent_dates) > cutoff_date:
        for ofile in older:
            if ofile.get("filingTo", "9999") < cutoff_date or not ofile.get("name"):
                continue
            try:
                raw = fetch_edgar_bytes(f"https://data.sec.gov/submissions/{ofile['name']}", rate_limiter=rate_limiter)
                _group(json.loads(raw.decode("utf-8")))
            except Exception as exc:
                logger.warning("Failed to fetch older filings file %s for %s: %s", ofile["name"], symbol, exc)
    return out


def _insert_event(data: dict[str, Any]) -> Sc13dEvent | None:
    """Insert one event idempotently (unique accession). Returns the row, or None when it already existed."""
    with get_db_session() as session:
        session.expire_on_commit = False
        try:
            with session.begin_nested():
                if session.query(Sc13dEvent.id).filter_by(accession=data["accession"]).first() is not None:
                    return None
                row = Sc13dEvent(
                    symbol=data["symbol"],
                    accession=data["accession"],
                    filed_at=data["filed_at"],
                    fund_like=bool(data["fund_like"]),
                    filers=data.get("filers"),
                    source_url=data.get("source_url"),
                )
                session.add(row)
                session.flush()
                return row
        except IntegrityError:
            return None


def touch_heartbeat(now: datetime | None = None) -> None:
    """Record that a scan finished OK (symbol ``_HEARTBEAT``, ``filed_at`` 1970, ``created_at`` refreshed)."""
    stamp = (now or datetime.now(UTC)).astimezone(UTC).replace(tzinfo=None)
    with get_db_session() as session:
        row = session.query(Sc13dEvent).filter_by(accession=HEARTBEAT_ACCESSION).first()
        if row is None:
            session.add(
                Sc13dEvent(
                    symbol="_HEARTBEAT",
                    accession=HEARTBEAT_ACCESSION,
                    filed_at=datetime(1970, 1, 1),
                    created_at=stamp,
                )
            )
        else:
            row.created_at = stamp


def scan(
    symbols: Sequence[str] | None = None,
    since_days: int = DEFAULT_SINCE_DAYS,
    now: datetime | None = None,
    rate_limiter: TokenBucket | None = None,
    fail_threshold: float = MAX_FAILURE_RATIO,
) -> list[Sc13dEvent]:
    """
    Scan ``symbols`` (default: the activist universe) for initial 13D filings since ``since_days`` ago.

    Returns the newly inserted rows. Raises RuntimeError when more than ``fail_threshold`` of the symbols fail, so
    a broken scanner is loud rather than a quiet market.
    """
    targets = list(ACTIVIST_UNIVERSE) if symbols is None else list(symbols)
    if not targets:
        return []
    if now is None:
        now_dt = datetime.now(UTC).replace(tzinfo=None)
    else:
        now_dt = now.astimezone(UTC).replace(tzinfo=None) if now.tzinfo is not None else now
    cutoff = now_dt - timedelta(days=since_days)
    limiter = rate_limiter if rate_limiter is not None else DEFAULT_RATE_LIMITER

    try:
        cik_map = load_sec_ticker_to_cik(rate_limiter=limiter)
    except Exception as exc:
        raise RuntimeError(f"Failed to load SEC company_tickers.json: {exc}") from exc

    def scan_symbol(sym: str) -> list[Sc13dEvent]:
        s = sym.strip().upper()
        cik = cik_map.get(s) or cik_map.get(s.replace(".", "-")) or cik_map.get(s.replace("-", "."))
        if not cik:
            raise LookupError(f"No CIK found for symbol {sym} in SEC mapping")
        raw = fetch_edgar_bytes(f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json", rate_limiter=limiter)
        cands = parse_13d_filings(json.loads(raw.decode("utf-8")), sym, cik, cutoff, rate_limiter=limiter)
        if not cands:
            return []
        with get_db_session() as session:
            seen = {
                a
                for (a,) in session.query(Sc13dEvent.accession)
                .filter(Sc13dEvent.accession.in_([c["accession"] for c in cands]))
                .all()
            }
        new_rows: list[Sc13dEvent] = []
        for c in cands:
            if c["accession"] in seen:
                continue
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{c['accession'].replace('-', '')}/{c['primary_doc']}"
            try:
                text = clean_html(fetch_edgar_bytes(url, rate_limiter=limiter).decode("utf-8", errors="replace"))
            except Exception as exc:
                # Leave it unstored so the next run retries; storing it unclassified would hide a fund filing.
                logger.warning("Failed to fetch 13D doc %s for %s: %s", url, sym, exc)
                continue
            names = reporting_names(text[:COVER_SEARCH_CHARS])
            row = _insert_event(
                {
                    "symbol": sym,
                    "accession": c["accession"],
                    "filed_at": c["filed_at"],
                    "fund_like": is_fund_like(names),
                    "filers": names[:FILERS_MAX_CHARS] if names else None,
                    "source_url": url,
                }
            )
            if row is not None:
                new_rows.append(row)
                logger.info("Inserted 13D for %s: %s fund_like=%s filers=%r", sym, c["accession"], row.fund_like, names)
        return new_rows

    def safe(sym: str) -> tuple[str, list[Sc13dEvent] | None]:
        try:
            return sym, scan_symbol(sym)
        except Exception as exc:
            logger.warning("Error scanning symbol %s: %s", sym, exc)
            return sym, None

    inserted: list[Sc13dEvent] = []
    failed: list[str] = []
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:
        for sym, rows in pool.map(safe, targets):
            if rows is None:
                failed.append(sym)
            else:
                inserted.extend(rows)

    if len(failed) / len(targets) > fail_threshold:
        msg = (
            f"13D scan failed: {len(failed)} of {len(targets)} symbols failed "
            f"({len(failed) / len(targets):.1%} > {fail_threshold:.0%}). First failures: {failed[:20]}"
        )
        logger.error(msg)
        raise RuntimeError(msg)
    return inserted
