"""
SEC Form 13F structured data set ingest (SUBMISSION, COVERPAGE, INFOTABLE).

Rules:
- Rows are keyed by acceptance (filing) date `filed_at`, never by period end,
  so a backtest can only see a filing from the day it was public.
- Amendments: the original filing is kept and so is the latest amendment per
  (cik, period_end). `select_effective` returns the amendment only for
  `as_of >= amendment.filed_at`; before that the original is the effective one.
- Option rows (PUTCALL set) are dropped.
- Value units: the SEC changed Form 13F VALUE from thousands of dollars to
  whole dollars for filings made on or after 2023-01-03 (the date the EDGAR
  13F XML schema change took effect). Decided by filing date, not period end.
- Non-equity / unmapped CUSIPs are only flagged (ticker stays NULL, mapped
  later through cusip_map).
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

# First filing date reporting VALUE in whole dollars (before: thousands).
VALUE_DOLLARS_CUTOVER = date(2023, 1, 3)

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
    """Return the reported VALUE in dollars, based on the filing date."""
    return float(value) * (1.0 if filed_on >= VALUE_DOLLARS_CUTOVER else 1000.0)


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
                "value_usd": normalise_value(value, f["filed_at"].date()),
                "shares": shares,
                "put_call": None,
            }
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
