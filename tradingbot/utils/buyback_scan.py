"""
SEC EDGAR Form 8-K buyback authorization scanner implementation.

Scans companies for Form 8-K filings with Item 8.01 or 7.01 containing share
repurchase or buyback authorization announcements, saving matching events to
the `buyback_events` table.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, time, timedelta
import json
import logging
import threading
import time as time_mod
from typing import Any
import urllib.error
import urllib.request

import pandas as pd
from sqlalchemy.exc import IntegrityError

from tradingbot.utils.buyback_rules import clean_html, extract_snippet, has_item_801_or_701, matches_buyback
from tradingbot.utils.db import BuybackEvent, get_db_session
from tradingbot.utils.universes import SP100

logger = logging.getLogger(__name__)

SEC_USER_AGENT = "tradingbot-research"
DEFAULT_REQ_PER_SEC = 5.0
DEFAULT_TIMEOUT = 15.0
DEFAULT_SINCE_DAYS = 5
MAX_FAILURE_RATIO = 0.20


class TokenBucket:
    """Thread-safe token bucket rate limiter."""

    def __init__(self, rate: float = DEFAULT_REQ_PER_SEC, capacity: float = 5.0):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_time = time_mod.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        if self.rate <= 0:
            return
        with self._lock:
            now = time_mod.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.last_time) * self.rate)
            self.last_time = now
            if self.tokens < 1.0:
                needed = 1.0 - self.tokens
                sleep_sec = needed / self.rate
                time_mod.sleep(sleep_sec)
                self.tokens = 0.0
                self.last_time = time_mod.monotonic()
            else:
                self.tokens -= 1.0


DEFAULT_RATE_LIMITER = TokenBucket(rate=DEFAULT_REQ_PER_SEC, capacity=5.0)

# In-memory ticker-to-CIK mapping cache per process run
_CIK_CACHE: dict[str, int] = {}


def clear_cik_cache() -> None:
    """Clear the in-memory ticker-to-CIK cache (for testing)."""
    _CIK_CACHE.clear()


def fetch_edgar_bytes(
    url: str,
    rate_limiter: TokenBucket | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = 1,
) -> bytes:
    """
    Fetch URL from SEC EDGAR with rate limiting, generic User-Agent, and retry.
    """
    limiter = rate_limiter if rate_limiter is not None else DEFAULT_RATE_LIMITER
    headers = {"User-Agent": SEC_USER_AGENT}
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            limiter.acquire()
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                logger.warning("Fetch failed for %s (attempt %d/%d), retrying: %s", url, attempt + 1, max_retries + 1, exc)
                time_mod.sleep(0.5)
            else:
                logger.error("Fetch failed for %s after %d retries: %s", url, max_retries, exc)
                raise

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"Failed to fetch {url}")


def parse_acceptance_datetime(dt_str: str | None, filing_date_str: str | None = None) -> datetime:
    """
    Parse EDGAR acceptanceDateTime into a naive UTC datetime.

    Per anti-look-ahead rules: if only a filing date is known, set timestamp to
    23:59:59 UTC of the filing date, guaranteeing entry at the next session close.
    """
    if dt_str:
        dt_str = str(dt_str).strip()
        if "T" in dt_str or "-" in dt_str:
            try:
                ts = pd.Timestamp(dt_str)
                if ts.tzinfo is not None:
                    ts = ts.tz_convert(UTC)
                else:
                    ts = ts.tz_localize(UTC)
                return ts.to_pydatetime().replace(tzinfo=None)
            except Exception:
                pass
        if len(dt_str) == 14 and dt_str.isdigit():
            try:
                ts = pd.to_datetime(dt_str, format="%Y%m%d%H%M%S", utc=True)
                return ts.to_pydatetime().replace(tzinfo=None)
            except Exception:
                pass

    if filing_date_str:
        d = pd.Timestamp(filing_date_str).date()
        return datetime.combine(d, time(23, 59, 59))

    raise ValueError(f"Unable to parse timestamp: dt_str={dt_str}, filing_date_str={filing_date_str}")


def load_sec_ticker_to_cik(rate_limiter: TokenBucket | None = None) -> dict[str, int]:
    """
    Load ticker to CIK mapping from SEC EDGAR company_tickers.json.
    Cached in memory per run.
    """
    if _CIK_CACHE:
        return _CIK_CACHE

    url = "https://www.sec.gov/files/company_tickers.json"
    raw = fetch_edgar_bytes(url, rate_limiter=rate_limiter)
    data = json.loads(raw.decode("utf-8"))

    mapping: dict[str, int] = {}
    for item in data.values():
        ticker = str(item["ticker"]).upper().strip()
        cik = int(item["cik_str"])
        mapping[ticker] = cik
        if "-" in ticker:
            mapping[ticker.replace("-", ".")] = cik
        if "." in ticker:
            mapping[ticker.replace(".", "-")] = cik

    _CIK_CACHE.update(mapping)
    return _CIK_CACHE


def parse_candidate_filings(
    submissions_data: dict[str, Any],
    symbol: str,
    cik: int,
    cutoff_dt: datetime,
    rate_limiter: TokenBucket | None = None,
) -> list[dict[str, Any]]:
    """
    Extract candidate Form 8-K filings with Item 8.01 or 7.01 filed since cutoff_dt.
    """
    candidates: list[dict[str, Any]] = []
    cutoff_date_str = cutoff_dt.date().isoformat()

    def _parse_group(group: dict[str, Any]) -> None:
        forms = group.get("form", [])
        dates = group.get("filingDate", [])
        accessions = group.get("accessionNumber", [])
        items_list = group.get("items", [])
        acceptances = group.get("acceptanceDateTime", [])
        primary_docs = group.get("primaryDocument", [])

        n = len(forms)
        for i in range(n):
            form = str(forms[i]).upper()
            if form != "8-K":
                continue

            fdate = str(dates[i])
            if fdate < cutoff_date_str:
                continue

            items_val = items_list[i] if i < len(items_list) else None
            if not has_item_801_or_701(items_val):
                continue

            pdoc = str(primary_docs[i]) if i < len(primary_docs) else ""
            if not pdoc:
                continue

            acc = str(accessions[i])
            acc_dt = str(acceptances[i]) if i < len(acceptances) else None
            try:
                filed_at = parse_acceptance_datetime(acc_dt, fdate)
            except Exception:
                continue

            if filed_at < cutoff_dt:
                continue

            candidates.append({
                "symbol": symbol,
                "cik": cik,
                "accession": acc,
                "filing_date": fdate,
                "filed_at": filed_at,
                "primary_doc": pdoc,
            })

    recent = submissions_data.get("filings", {}).get("recent", {})
    if recent:
        _parse_group(recent)

    # Check older files in filings.files if recent doesn't reach cutoff_date_str
    older_files = submissions_data.get("filings", {}).get("files", [])
    recent_dates = recent.get("filingDate", [])
    if older_files and recent_dates and min(recent_dates) > cutoff_date_str:
        for ofile in older_files:
            filing_to = ofile.get("filingTo", "")
            if filing_to and filing_to < cutoff_date_str:
                continue
            fname = ofile.get("name")
            if not fname:
                continue
            of_url = f"https://data.sec.gov/submissions/{fname}"
            try:
                of_raw = fetch_edgar_bytes(of_url, rate_limiter=rate_limiter)
                of_data = json.loads(of_raw.decode("utf-8"))
                _parse_group(of_data)
            except Exception as exc:
                logger.warning("Failed to fetch older filings file %s for %s: %s", fname, symbol, exc)

    return candidates


def _insert_event(event_data: dict[str, Any]) -> BuybackEvent | None:
    """
    Insert a new BuybackEvent row idempotently into the database.
    Returns the created instance if inserted, or None if skipped/conflicted.
    """
    with get_db_session() as session:
        session.expire_on_commit = False
        try:
            with session.begin_nested():
                existing = session.query(BuybackEvent.id).filter_by(accession=event_data["accession"]).first()
                if existing is not None:
                    return None

                row = BuybackEvent(
                    symbol=event_data["symbol"],
                    accession=event_data["accession"],
                    filed_at=event_data["filed_at"],
                    source_url=event_data.get("source_url"),
                    snippet=event_data.get("snippet"),
                )
                session.add(row)
                session.flush()
                return row
        except IntegrityError:
            return None


def scan(
    symbols: Sequence[str] | None = None,
    since_days: int = DEFAULT_SINCE_DAYS,
    now: datetime | None = None,
    rate_limiter: TokenBucket | None = None,
    fail_threshold: float = MAX_FAILURE_RATIO,
) -> list[BuybackEvent]:
    """
    Scan symbols for Form 8-K buyback authorization filings since `since_days` ago.

    Fetches submissions from SEC EDGAR, filters 8-Ks with Item 8.01 or 7.01,
    downloads primary documents matching buyback regex, and inserts new records
    into `buyback_events` idempotently.

    Args:
        symbols: Universe to scan. Defaults to SP100.
        since_days: Lookback horizon in days (default: 5).
        now: Current time reference (default: UTC now).
        rate_limiter: Rate limiter to use (default: 5 req/s token bucket).
        fail_threshold: Max fraction of symbols allowed to fail (default: 0.20).

    Returns:
        List of newly inserted BuybackEvent instances.

    Raises:
        RuntimeError: If more than fail_threshold (20%) of symbols fail.
    """
    if symbols is None:
        target_symbols = list(SP100)
    else:
        target_symbols = list(symbols)

    if not target_symbols:
        return []

    if now is None:
        now_dt = datetime.now(UTC).replace(tzinfo=None)
    elif now.tzinfo is not None:
        now_dt = now.astimezone(UTC).replace(tzinfo=None)
    else:
        now_dt = now

    cutoff_dt = now_dt - timedelta(days=since_days)

    limiter = rate_limiter if rate_limiter is not None else DEFAULT_RATE_LIMITER

    try:
        cik_map = load_sec_ticker_to_cik(rate_limiter=limiter)
    except Exception as exc:
        logger.error("Failed to load SEC ticker-to-CIK mapping: %s", exc)
        raise RuntimeError(f"Failed to load SEC company_tickers.json: {exc}") from exc

    inserted_events: list[BuybackEvent] = []
    failed_symbols: list[str] = []

    for sym in target_symbols:
        sym_clean = sym.strip().upper()
        try:
            cik = cik_map.get(sym_clean)
            if not cik:
                # Try alternatives with dots / dashes
                if "." in sym_clean:
                    cik = cik_map.get(sym_clean.replace(".", "-"))
                elif "-" in sym_clean:
                    cik = cik_map.get(sym_clean.replace("-", "."))

            if not cik:
                logger.warning("No CIK found for symbol %s in SEC mapping", sym)
                failed_symbols.append(sym)
                continue

            cik_str = str(cik).zfill(10)
            submissions_url = f"https://data.sec.gov/submissions/CIK{cik_str}.json"
            raw_sub = fetch_edgar_bytes(submissions_url, rate_limiter=limiter)
            sub_data = json.loads(raw_sub.decode("utf-8"))

            candidates = parse_candidate_filings(
                submissions_data=sub_data,
                symbol=sym,
                cik=cik,
                cutoff_dt=cutoff_dt,
                rate_limiter=limiter,
            )
            if not candidates:
                continue

            # Query already stored accessions to skip fetching existing documents
            candidate_accessions = [c["accession"] for c in candidates]
            with get_db_session() as session:
                existing_accessions = {
                    acc
                    for (acc,) in session.query(BuybackEvent.accession)
                    .filter(BuybackEvent.accession.in_(candidate_accessions))
                    .all()
                }

            unseen_candidates = [c for c in candidates if c["accession"] not in existing_accessions]
            if not unseen_candidates:
                continue

            for cand in unseen_candidates:
                acc_clean = cand["accession"].replace("-", "")
                pdoc = cand["primary_doc"]
                doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_clean}/{pdoc}"

                try:
                    doc_bytes = fetch_edgar_bytes(doc_url, rate_limiter=limiter)
                except Exception as exc:
                    logger.warning("Failed to fetch primary doc %s for %s (%s): %s", pdoc, sym, cand["accession"], exc)
                    continue

                text = clean_html(doc_bytes.decode("utf-8", errors="replace"))
                if not matches_buyback(text):
                    continue

                snippet = extract_snippet(text, max_chars=300)
                event_data = {
                    "symbol": sym,
                    "accession": cand["accession"],
                    "filed_at": cand["filed_at"],
                    "source_url": doc_url,
                    "snippet": snippet,
                }
                new_row = _insert_event(event_data)
                if new_row is not None:
                    inserted_events.append(new_row)
                    logger.info("Inserted BuybackEvent for %s: accession %s, filed_at %s", sym, cand["accession"], cand["filed_at"])

        except Exception as exc:
            logger.warning("Error scanning symbol %s: %s", sym, exc)
            failed_symbols.append(sym)

    total_symbols = len(target_symbols)
    fail_count = len(failed_symbols)
    if total_symbols > 0 and (fail_count / total_symbols) > fail_threshold:
        msg = (
            f"Buyback scan failed: {fail_count} of {total_symbols} symbols failed "
            f"({fail_count / total_symbols:.1%} > {fail_threshold:.0%} threshold). "
            f"Failed symbols: {failed_symbols}"
        )
        logger.error(msg)
        raise RuntimeError(msg)

    return inserted_events
