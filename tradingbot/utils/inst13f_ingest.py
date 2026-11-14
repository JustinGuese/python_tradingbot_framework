"""
SEC Form 13F structured data set ingest (SUBMISSION, COVERPAGE, INFOTABLE).

Rules:
- Rows are keyed by acceptance (filing) date `filed_at`, never by period end,
  so a backtest can only see a filing from the day it was public.
- Amendments: the original filing is kept and so is the latest amendment per
  (cik, period_end). `select_effective` returns the amendment only for
  `as_of >= amendment.filed_at`; before that the original is the effective one.
- Option rows (PUTCALL set) are dropped.
- Value units (thousands vs whole dollars) are detected per filing from the
  implied price per share (value/shares) against the cross-filer median per
  CUSIP in the same load; a filing ~1000x below the others is in thousands.
  The SEC page does not document the change date, so the date rule
  (VALUE_DOLLARS_CUTOVER) is only a logged fallback when overlaps are too few.
- Non-equity / unmapped CUSIPs are only flagged (ticker stays NULL, mapped
  later through cusip_map).
"""

from __future__ import annotations

import csv
import io
import logging
import math
import statistics
from collections import defaultdict
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Unverified fallback: first filing date believed to report VALUE in dollars.
VALUE_DOLLARS_CUTOVER = date(2023, 1, 3)
MIN_OVERLAP_CUSIPS = 3  # CUSIPs shared with other filers needed to judge a filing
UNIT_GAP_DECADES = 2.0  # log10 gap separating the thousands group (true gap is 3)

_DATE_FORMATS = ("%d-%b-%Y", "%Y-%m-%d", "%m/%d/%Y")


def _parse_date(text: str) -> date:
    text = text.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unparseable date: {text!r}")


def _read_tsv(text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    return [{(k or "").strip().upper(): (v or "").strip() for k, v in row.items()} for row in reader]


def normalise_value(value: float, filed_on: date) -> float:
    """Date-rule fallback: VALUE in dollars, assuming the cutover date."""
    return float(value) * (1.0 if filed_on >= VALUE_DOLLARS_CUTOVER else 1000.0)


def detect_thousands(rows: list[dict[str, Any]]) -> dict[str, bool | None]:
    """Per accession: True if VALUE is in thousands, False if dollars, None if unknown.

    Each filing's offset is the median over its CUSIPs of log10(implied price)
    minus log10(cross-filer median implied price for that CUSIP). Filings whose
    offsets sit more than UNIT_GAP_DECADES below the top group are in thousands.
    """
    by_cusip: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["shares"] > 0 and r["raw_value"] > 0:
            by_cusip[r["cusip"]][r["accession"]].append(math.log10(r["raw_value"] / r["shares"]))
    offsets: dict[str, list[float]] = defaultdict(list)
    for per_filer in by_cusip.values():
        if len(per_filer) < 2:
            continue
        logs = {a: statistics.median(v) for a, v in per_filer.items()}
        ref = statistics.median(logs.values())
        for a, lp in logs.items():
            offsets[a].append(lp - ref)
    off = {a: statistics.median(v) for a, v in offsets.items() if len(v) >= MIN_OVERLAP_CUSIPS}
    out: dict[str, bool | None] = {r["accession"]: None for r in rows}
    if len(off) < 2:
        return out
    top = max(off.values())
    for a, d in off.items():
        out[a] = (top - d) > UNIT_GAP_DECADES
    # If every judged filing sits in one group the unit is unidentifiable from
    # relative prices alone; leave to the date-rule fallback.
    if not any(out[a] for a in off) or all(out[a] for a in off):
        return {a: None for a in out}
    return out


def parse_quarter(submission: str, coverpage: str, infotable: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse the three tab-separated files into (filings, holdings) dict lists."""
    cover = {r["ACCESSION_NUMBER"]: r for r in _read_tsv(coverpage)}
    filings: dict[str, dict[str, Any]] = {}
    for r in _read_tsv(submission):
        sub_type = r.get("SUBMISSIONTYPE", "")
        if not sub_type.startswith("13F-HR"):
            continue
        acc = r["ACCESSION_NUMBER"]
        c = cover.get(acc, {})
        # A "NEW HOLDINGS" amendment lists only the added positions; treating it as
        # a restatement would replace the full original filing with a partial one.
        if sub_type.endswith("/A") and c.get("AMENDMENTTYPE", "").upper().startswith("NEW"):
            continue
        filed = _parse_date(r["FILING_DATE"])
        filings[acc] = {
            "accession": acc,
            "cik": int(r["CIK"]),
            "filer_name": c.get("FILINGMANAGER_NAME", ""),
            "period_end": _parse_date(r["PERIODOFREPORT"]),
            "filed_at": datetime.combine(filed, datetime.min.time()),
            "is_amendment": sub_type.endswith("/A") or c.get("ISAMENDMENT", "").upper() in ("Y", "TRUE", "1"),
        }
    holdings: list[dict[str, Any]] = []
    for r in _read_tsv(infotable):
        f = filings.get(r["ACCESSION_NUMBER"])
        if f is None or r.get("PUTCALL"):
            continue
        try:
            value = float(r["VALUE"])
            shares = float(r.get("SSHPRNAMT") or 0)
        except ValueError:
            continue
        holdings.append(
            {
                "accession": f["accession"],
                "cusip": r["CUSIP"].upper(),
                "ticker": None,
                "raw_value": value,
                "shares": shares,
                "put_call": None,
            }
        )
    units = detect_thousands(holdings)
    fallback = 0
    for h in holdings:
        thousands = units.get(h["accession"])
        raw = h.pop("raw_value")
        if thousands is None:
            fallback += 1
            h["value_usd"] = normalise_value(raw, filings[h["accession"]]["filed_at"].date())
        else:
            h["value_usd"] = raw * (1000.0 if thousands else 1.0)
    if fallback:
        logger.warning(
            "13F units: %d holdings used the unverified date rule (%s) for lack of cross-filer overlap",
            fallback,
            VALUE_DOLLARS_CUTOVER,
        )
    return list(filings.values()), holdings


def keep_latest_amendments(filings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep originals and only the latest amendment per (cik, period_end)."""
    latest: dict[tuple[int, date], dict[str, Any]] = {}
    out = [f for f in filings if not f["is_amendment"]]
    for f in filings:
        if f["is_amendment"]:
            key = (f["cik"], f["period_end"])
            if key not in latest or f["filed_at"] > latest[key]["filed_at"]:
                latest[key] = f
    return out + list(latest.values())


def select_effective(filings: list[dict[str, Any]], as_of: datetime) -> dict[tuple[int, date], dict[str, Any]]:
    """Per (cik, period_end), the latest filing accepted at or before `as_of`."""
    best: dict[tuple[int, date], dict[str, Any]] = {}
    for f in filings:
        if f["filed_at"] > as_of:
            continue
        key = (f["cik"], f["period_end"])
        if key not in best or f["filed_at"] >= best[key]["filed_at"]:
            best[key] = f
    return best


def ingest_quarter(submission: str, coverpage: str, infotable: str, session: Any = None) -> tuple[int, int]:
    """Parse and persist one quarter idempotently. Returns (filings, holdings) inserted."""
    from tradingbot.utils.db import Inst13fFiling, Inst13fHolding, get_db_session

    filings, holdings = parse_quarter(submission, coverpage, infotable)
    filings = keep_latest_amendments(filings)
    keep = {f["accession"] for f in filings}
    holdings = [h for h in holdings if h["accession"] in keep]

    def _run(s: Any) -> tuple[int, int]:
        existing = {a for (a,) in s.query(Inst13fFiling.accession).filter(Inst13fFiling.accession.in_(keep)).all()}
        new_f = [f for f in filings if f["accession"] not in existing]
        new_acc = {f["accession"] for f in new_f}
        s.add_all(Inst13fFiling(**f) for f in new_f)
        new_h = [h for h in holdings if h["accession"] in new_acc]
        s.add_all(Inst13fHolding(**h) for h in new_h)
        s.commit()
        return len(new_f), len(new_h)

    if session is not None:
        return _run(session)
    with get_db_session() as s:
        return _run(s)
