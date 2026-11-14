"""
CUSIP -> US equity ticker mapping via the OpenFIGI v3 mapping API.

Results are cached in the ``cusip_map`` table. Only CUSIPs are ever sent to the
API. Unresolved CUSIPs are stored with ticker NULL and are not re-queried until
``retry_days`` have passed.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

import requests  # type: ignore[import-untyped]
from sqlalchemy.orm import Session

from .db import CusipMap

logger = logging.getLogger(__name__)

OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
BATCH_NO_KEY = 10
BATCH_WITH_KEY = 100
# OpenFIGI limits: 25 requests/min without key, 25 per 6 seconds with key.
MIN_INTERVAL_NO_KEY = 2.5
MIN_INTERVAL_WITH_KEY = 0.3
DEFAULT_RETRY_DAYS = 30
SOURCE = "openfigi"

PostFn = Callable[[list[dict[str, str]]], list[dict[str, Any]]]


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _batch_size() -> int:
    return BATCH_WITH_KEY if os.environ.get("OPENFIGI_API_KEY") else BATCH_NO_KEY


def _default_post(jobs: list[dict[str, str]]) -> list[dict[str, Any]]:
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("OPENFIGI_API_KEY")
    if key:
        headers["X-OPENFIGI-APIKEY"] = key
    for attempt in range(3):
        resp = requests.post(OPENFIGI_URL, json=jobs, headers=headers, timeout=30)
        if resp.status_code == 429:
            time.sleep(float(resp.headers.get("Retry-After", 10)) * (attempt + 1))
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError("OpenFIGI rate limit not clearing after retries")


def _pick_ticker(result: dict[str, Any]) -> str | None:
    """Prefer a US common stock listing; None when nothing usable came back."""
    data = result.get("data") or []
    for pref in (
        lambda d: d.get("exchCode") == "US" and d.get("securityType") == "Common Stock",
        lambda d: d.get("exchCode") == "US",
    ):
        for d in data:
            if pref(d) and d.get("ticker"):
                return str(d["ticker"]).upper()
    return None


def resolve_cusips(
    session: Session,
    cusips: Iterable[str],
    retry_days: int = DEFAULT_RETRY_DAYS,
    post: PostFn | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, str | None]:
    """Return {cusip: ticker or None}, querying OpenFIGI only for unknown CUSIPs.

    Cached resolved rows are never re-queried; cached unresolved rows are
    re-queried only once older than ``retry_days``.
    """
    post = post or _default_post
    wanted = list(dict.fromkeys(c.strip().upper() for c in cusips if c and c.strip()))
    result: dict[str, str | None] = {}
    if not wanted:
        return result

    existing = {r.cusip: r for r in session.query(CusipMap).filter(CusipMap.cusip.in_(wanted)).all()}
    cutoff = _now() - timedelta(days=retry_days)
    todo: list[str] = []
    for c in wanted:
        row = existing.get(c)
        if row is None or (row.ticker is None and row.mapped_at < cutoff):
            todo.append(c)
        else:
            result[c] = row.ticker

    size = _batch_size()
    interval = MIN_INTERVAL_WITH_KEY if os.environ.get("OPENFIGI_API_KEY") else MIN_INTERVAL_NO_KEY
    for i in range(0, len(todo), size):
        batch = todo[i : i + size]
        if i:
            sleep(interval)
        # Only the CUSIP is sent; nothing else.
        responses = post([{"idType": "ID_CUSIP", "idValue": c} for c in batch])
        if len(responses) != len(batch):
            raise RuntimeError(f"OpenFIGI returned {len(responses)} results for {len(batch)} CUSIPs")
        for c, res in zip(batch, responses):
            ticker = _pick_ticker(res)
            row = existing.get(c)
            if row is None:
                row = CusipMap(cusip=c)
                session.add(row)
                existing[c] = row
            row.ticker = ticker
            row.source = SOURCE
            row.mapped_at = _now()
            result[c] = ticker
        session.commit()
    return result


def coverage(session: Session, holdings: Iterable[tuple[str, float]]) -> dict[str, Any]:
    """Share of a dollar-weighted (cusip, value) list that has a cached ticker.

    Reads the cache only (no API calls). Returns coverage in [0, 1] plus the
    unresolved CUSIPs ordered by dollar weight.
    """
    weights: dict[str, float] = {}
    for c, v in holdings:
        c = c.strip().upper()
        weights[c] = weights.get(c, 0.0) + float(v)
    total = sum(weights.values())
    rows = {
        r.cusip: r.ticker for r in session.query(CusipMap).filter(CusipMap.cusip.in_(list(weights))).all()
    }
    resolved = sum(v for c, v in weights.items() if rows.get(c))
    unresolved = sorted(((c, v) for c, v in weights.items() if not rows.get(c)), key=lambda x: -x[1])
    return {
        "coverage": resolved / total if total else 0.0,
        "total_value": total,
        "unresolved": unresolved,
    }
