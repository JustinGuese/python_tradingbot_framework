"""
Pure business rules for Form 8-K buyback authorization events.

Handles regex matching, item filtering, entry session calculation,
holding period tracking across NYSE sessions, and rolling window deduplication.
"""

from __future__ import annotations

import html
import re
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from tradingbot.utils.market_calendar import (
    is_session,
    next_session,
    session_close_utc,
    sessions_between,
)

NEW_YORK = ZoneInfo("America/New_York")

# Ported from scripts/onetime_event_buyback_study.py::BUYBACK_REGEX
BUYBACK_REGEX = re.compile(
    r"("
    r"(?:share|stock)\s+(?:repurchase|buyback)\s+(?:program|plan|authorization|facility)"
    r"|(?:authorized|approved|announced)\b[^\.\n;]{0,100}\b(?:share|stock)?\s*(?:repurchase|buyback)"
    r"|(?:repurchase|buyback)\s+(?:of\s+up\s+to\s+)?(?:\$[\d\.]+\s*(?:billion|million)?|\d+\s*(?:billion|million)\s*(?:shares|dollars))"
    r"|(?:repurchase|buyback)\s+(?:program|authorization)\s+of\s+up\s+to"
    r")",
    re.IGNORECASE,
)


def matches_buyback(text: str | None) -> bool:
    """Test if text contains buyback authorization terms."""
    if not text:
        return False
    return bool(BUYBACK_REGEX.search(text))


# Alias for backward-compatibility with study function name
matches_buyback_regex = matches_buyback


def has_item_801_or_701(items_val: object) -> bool:
    """Check if 8-K filing item is Item 8.01 (Other Events) or 7.01 (Reg FD)."""
    if items_val is None:
        return False
    if isinstance(items_val, (list, tuple, set)):
        return any("8.01" in str(x) or "7.01" in str(x) for x in items_val)
    s = str(items_val)
    return "8.01" in s or "7.01" in s


def clean_html(raw_html: str | None) -> str:
    """Strip HTML tags and unescape text."""
    if not raw_html:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html.unescape(text)
    return " ".join(text.split())


def extract_snippet(text: str, max_chars: int = 300) -> str:
    """Extract up to max_chars around the first buyback match in text."""
    if not text:
        return ""
    m = BUYBACK_REGEX.search(text)
    if not m:
        return text[:max_chars].strip()
    start_pos, end_pos = m.span()
    match_len = end_pos - start_pos
    if match_len >= max_chars:
        return text[start_pos : start_pos + max_chars].strip()
    rem = max_chars - match_len
    before = rem // 2
    after = rem - before
    s_idx = max(0, start_pos - before)
    e_idx = min(len(text), end_pos + after)
    if s_idx == 0:
        e_idx = min(len(text), max_chars)
    elif e_idx == len(text):
        s_idx = max(0, len(text) - max_chars)
    return " ".join(text[s_idx:e_idx].split())


def _to_utc_datetime(dt_val: datetime | str | pd.Timestamp) -> datetime:
    """Convert input to an aware UTC datetime."""
    if isinstance(dt_val, str):
        t = pd.Timestamp(dt_val)
    elif isinstance(dt_val, pd.Timestamp):
        t = dt_val
    elif isinstance(dt_val, datetime):
        t = pd.Timestamp(dt_val)
    else:
        raise TypeError(f"Unsupported datetime type: {type(dt_val)}")

    if t.tzinfo is None:
        return t.tz_localize(UTC).to_pydatetime()
    return t.tz_convert(UTC).to_pydatetime()


def entry_session(filed_at_utc: datetime | str | pd.Timestamp) -> date:
    """
    First NYSE session whose close is strictly after filed_at_utc.

    Uses utils/market_calendar helpers. No look-ahead: if filing arrives before
    or at close, entry is the first session close strictly after acceptance time.
    """
    dt_utc = _to_utc_datetime(filed_at_utc)
    cand = dt_utc.astimezone(NEW_YORK).date()
    if is_session(cand):
        close = session_close_utc(cand)
        if close is not None and close > dt_utc:
            return cand
    return next_session(cand)


def _extract_symbol_and_entry(event: Any) -> tuple[str, date]:
    """Helper to extract symbol and entry_session date from dict, model, or row."""
    if isinstance(event, dict):
        sym = str(event["symbol"])
        ent = event.get("entry_session")
        if ent is None:
            filed = event.get("filed_at") or event.get("acceptance_dt") or event.get("created_at")
            if filed is None:
                raise ValueError(f"Event missing filed_at or entry_session: {event}")
            ent = entry_session(filed)
    else:
        sym = str(event.symbol)
        ent = getattr(event, "entry_session", None)
        if ent is None:
            filed = (
                getattr(event, "filed_at", None)
                or getattr(event, "acceptance_dt", None)
                or getattr(event, "created_at", None)
            )
            if filed is None:
                raise ValueError(f"Event missing filed_at or entry_session: {event}")
            ent = entry_session(filed)

    if isinstance(ent, str):
        ent = pd.Timestamp(ent).date()
    elif isinstance(ent, (datetime, pd.Timestamp)):
        ent = ent.date()
    return sym, ent


def active_symbols(
    events: Sequence[Any] | pd.DataFrame | None,
    bar_day: date | datetime | str | pd.Timestamp,
    hold_sessions: int = 10,
) -> list[str]:
    """
    Symbols whose position is held at bar_day's close.

    Condition: entry_session <= bar_day < entry_session + hold_sessions (NYSE sessions).
    Returns deduplicated list of symbols preserving order.
    """
    if events is None:
        return []

    if isinstance(events, pd.DataFrame):
        if events.empty:
            return []
        event_list: list[Any] = events.to_dict(orient="records")
    else:
        event_list = list(events)

    if not event_list:
        return []

    if isinstance(bar_day, str):
        bar_date = pd.Timestamp(bar_day).date()
    elif isinstance(bar_day, (datetime, pd.Timestamp)):
        bar_date = bar_day.date()
    else:
        bar_date = bar_day

    active: list[str] = []
    for ev in event_list:
        sym, entry = _extract_symbol_and_entry(ev)
        if bar_date >= entry and 0 <= sessions_between(entry, bar_date) < hold_sessions:
            active.append(sym)

    return list(dict.fromkeys(active))


def _extract_ts(event: Any) -> datetime:
    """Extract aware UTC datetime from event."""
    if isinstance(event, dict):
        val = event.get("filed_at") or event.get("acceptance_dt") or event.get("created_at")
    else:
        val = (
            getattr(event, "filed_at", None)
            or getattr(event, "acceptance_dt", None)
            or getattr(event, "created_at", None)
        )
    if val is None:
        raise ValueError(f"Event missing filed_at timestamp: {event}")
    return _to_utc_datetime(val)


def _extract_symbol(event: Any) -> str:
    """Extract symbol from event."""
    if isinstance(event, dict):
        return str(event["symbol"])
    return str(event.symbol)


def dedupe_recent(
    events: Sequence[Any] | pd.DataFrame,
    window_days: int = 30,
) -> list[Any] | pd.DataFrame:
    """
    Drop an event if the same symbol had one in the previous window_days (default 30 days).
    """
    if isinstance(events, pd.DataFrame):
        if events.empty:
            return events.copy()
        col = (
            "filed_at"
            if "filed_at" in events.columns
            else ("acceptance_dt" if "acceptance_dt" in events.columns else "created_at")
        )
        df = events.copy()
        df["_ts_temp"] = pd.to_datetime(df[col], utc=True)
        df = df.sort_values("_ts_temp").reset_index(drop=True)
        kept_indices: list[int] = []
        last_by_sym: dict[str, pd.Timestamp] = {}
        for idx, row in df.iterrows():
            sym = str(row["symbol"])
            ts = row["_ts_temp"]
            if sym not in last_by_sym:
                kept_indices.append(idx)
                last_by_sym[sym] = ts
            else:
                diff_days = (ts - last_by_sym[sym]).total_seconds() / 86400.0
                if diff_days >= window_days:
                    kept_indices.append(idx)
                    last_by_sym[sym] = ts
        return df.iloc[kept_indices].drop(columns=["_ts_temp"]).reset_index(drop=True)

    if not events:
        return []

    sorted_events = sorted(events, key=_extract_ts)
    kept: list[Any] = []
    last_by_sym_dt: dict[str, datetime] = {}
    for ev in sorted_events:
        sym = _extract_symbol(ev)
        ts = _extract_ts(ev)
        if sym not in last_by_sym_dt:
            kept.append(ev)
            last_by_sym_dt[sym] = ts
        else:
            diff_days = (ts - last_by_sym_dt[sym]).total_seconds() / 86400.0
            if diff_days >= window_days:
                kept.append(ev)
                last_by_sym_dt[sym] = ts
    return kept
