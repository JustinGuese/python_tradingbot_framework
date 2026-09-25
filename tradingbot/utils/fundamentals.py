"""
Point-in-time fundamentals: capture daily, read as-of a date.

yfinance serves fundamentals (P/E, EV/EBITDA, FCF, institutional ownership, ...)
only as of *now*. Using those values in a backtest of 2021 would be look-ahead,
so the only honest history is one we record ourselves, one row per symbol per
day, into `stock_fundamentals`.

Readers must cope with that history starting on the day capture began:
`get_fundamentals` raises `FundamentalsNotAvailable` for any date before the
first snapshot (or when the newest one is too old), and
`get_fundamentals_batch` maps that to None. A strategy should treat None as
"neutral", never as zero — a P/E of 0 is a claim, not an absence.
"""

from __future__ import annotations

import logging
import math
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd
import yfinance as yf

from .db import StockFundamentalsSnapshot, get_db_session

logger = logging.getLogger(__name__)

# Delay between symbols to stay under yfinance's rate limits (same as
# stock_fundamentals_loader).
SYMBOL_DELAY_SECONDS = 0.5

# Column -> yfinance `info` keys, first present one wins.
FIELD_KEYS: dict[str, tuple[str, ...]] = {
    "market_cap": ("marketCap",),
    "enterprise_value": ("enterpriseValue",),
    "trailing_pe": ("trailingPE",),
    "forward_pe": ("forwardPE",),
    "peg_ratio": ("pegRatio", "trailingPegRatio"),
    "price_to_book": ("priceToBook",),
    "price_to_sales": ("priceToSalesTrailing12Months",),
    "ev_to_ebitda": ("enterpriseToEbitda",),
    "ebitda": ("ebitda",),
    "free_cashflow": ("freeCashflow",),
    "return_on_equity": ("returnOnEquity",),
    "debt_to_equity": ("debtToEquity",),
    "shares_outstanding": ("sharesOutstanding",),
    "float_shares": ("floatShares",),
    "held_pct_institutions": ("heldPercentInstitutions",),
    "held_pct_insiders": ("heldPercentInsiders",),
    "short_pct_float": ("shortPercentOfFloat",),
}
FIELDS: tuple[str, ...] = (*FIELD_KEYS, "fcf_yield")


class FundamentalsNotAvailable(LookupError):
    """No usable snapshot for this symbol as of this date — typically a date
    before capture began. Not an error in the data: an absence of it."""


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _json_safe(value: Any) -> Any:
    """Drop what Postgres JSON rejects: NaN/Infinity floats (json.dumps emits
    them as bare tokens) and anything not natively serialisable."""
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)


def parse_info(info: dict[str, Any]) -> dict[str, float | None]:
    """Map a yfinance `info` dict onto the modelled columns. Never raises on a missing key."""
    fields: dict[str, float | None] = {}
    for column, keys in FIELD_KEYS.items():
        fields[column] = next((v for v in (_to_float(info.get(k)) for k in keys) if v is not None), None)
    mcap, fcf = fields["market_cap"], fields["free_cashflow"]
    fields["fcf_yield"] = fcf / mcap if mcap and fcf is not None and mcap > 0 else None
    return fields


def fetch_fundamentals(symbol: str) -> tuple[dict[str, float | None], dict[str, Any]]:
    """Fetch today's fundamentals for `symbol`: (parsed fields, raw info)."""
    info = yf.Ticker(symbol).info or {}
    return parse_info(info), info


def snapshot_fundamentals(
    symbols: list[str] | tuple[str, ...],
    snapshot_date: date | None = None,
    delay_seconds: float = SYMBOL_DELAY_SECONDS,
) -> dict[str, list[str]]:
    """
    Record one snapshot per symbol for `snapshot_date` (default: today, UTC).

    Idempotent: a rerun on the same day updates the row instead of duplicating
    it. Select-then-write rather than a dialect upsert, so the SQLite test
    doubles exercise the same code path Postgres runs.

    Returns:
        {"written": [...], "failed": [...], "skipped": [...]} — kept distinct.
        failed = the fetch raised; skipped = yfinance answered but with none of
        the modelled fields (delisted, or an ETF), so nothing was stored.
    """
    day = snapshot_date or datetime.now(UTC).date()
    result: dict[str, list[str]] = {"written": [], "failed": [], "skipped": []}

    for i, symbol in enumerate(symbols):
        if i and delay_seconds:
            time.sleep(delay_seconds)
        try:
            fields, info = fetch_fundamentals(symbol)
        except Exception as exc:
            logger.warning("fundamentals: fetch failed for %s: %s", symbol, exc)
            result["failed"].append(symbol)
            continue
        if all(v is None for v in fields.values()):
            logger.warning("fundamentals: no usable fields for %s — skipped", symbol)
            result["skipped"].append(symbol)
            continue

        with get_db_session() as session:
            row = session.query(StockFundamentalsSnapshot).filter_by(symbol=symbol, snapshot_date=day).first()
            if row is None:
                row = StockFundamentalsSnapshot(symbol=symbol, snapshot_date=day)
                session.add(row)
            for column, value in fields.items():
                setattr(row, column, value)
            row.info = _json_safe(info)
        result["written"].append(symbol)

    return result


def _as_date(as_of: date | datetime | pd.Timestamp | str) -> date:
    if isinstance(as_of, datetime):
        return as_of.date()
    if isinstance(as_of, date):
        return as_of
    return pd.Timestamp(as_of).date()


def _latest_rows(symbols: list[str], as_of: date, max_age_days: int) -> dict[str, dict[str, Any]]:
    """Newest snapshot per symbol within [as_of - max_age_days, as_of], as plain dicts."""
    earliest = as_of - timedelta(days=max_age_days)
    latest: dict[str, dict[str, Any]] = {}
    with get_db_session() as session:
        rows = (
            session.query(StockFundamentalsSnapshot)
            .filter(
                StockFundamentalsSnapshot.symbol.in_(symbols),
                StockFundamentalsSnapshot.snapshot_date >= earliest,
                StockFundamentalsSnapshot.snapshot_date <= as_of,
            )
            .order_by(StockFundamentalsSnapshot.snapshot_date)
            .all()
        )
        # Extract inside the session: ORM rows detach when it closes.
        for row in rows:
            latest[row.symbol] = {
                "symbol": row.symbol,
                "snapshot_date": row.snapshot_date,
                **{f: getattr(row, f) for f in FIELDS},
            }
    return latest


def get_fundamentals(symbol: str, as_of: date | datetime | pd.Timestamp | str, max_age_days: int = 7) -> dict[str, Any]:
    """
    The latest snapshot for `symbol` on or before `as_of`.

    Raises:
        FundamentalsNotAvailable: no snapshot within `max_age_days` before
            `as_of` — every date before capture began, or a capture outage.
    """
    day = _as_date(as_of)
    row = _latest_rows([symbol], day, max_age_days).get(symbol)
    if row is None:
        raise FundamentalsNotAvailable(
            f"No fundamentals snapshot for {symbol} between {day - timedelta(days=max_age_days)} and {day}"
        )
    return row


def get_fundamentals_batch(
    symbols: list[str] | tuple[str, ...], as_of: date | datetime | pd.Timestamp | str, max_age_days: int = 7
) -> dict[str, dict[str, Any] | None]:
    """Lenient `get_fundamentals` for many symbols: None means not available."""
    found = _latest_rows(list(symbols), _as_date(as_of), max_age_days)
    return {s: found.get(s) for s in symbols}
