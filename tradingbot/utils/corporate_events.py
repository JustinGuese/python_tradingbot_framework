"""
Earnings dates (with their time of day) and ex-dividend dates, kept in the DB.

The option bots used to ask yfinance for these on every call. That made every
run depend on yfinance being up, and it recomputed each report's timing from
the timestamp every time. A daily job (corporateeventssnapshot, 12:00 UTC)
now stores them:
  * stock_earnings: report timestamp, EPS, surprise, and after_close. Rows are
    upserted, so an estimate-only row gets its actuals once the report is out.
  * dividend_events: past ex-dates with the amount paid, plus the next
    announced ex-date with the last amount.
  * corporate_event_refresh: when each symbol was last refreshed.

Readers (options.earnings_events, next_earnings_date, earnings_history,
next_dividend) call stored_events() first. It answers only for a symbol
refreshed within MAX_STALE_DAYS, and then an empty list is a real answer (no
dividend). Otherwise they fall back to yfinance and log a warning.

This module must not import utils/options.py, which imports it.
"""

import contextlib
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import yfinance as yf

from .db import CorporateEventRefresh, DividendEvent, StockEarnings, get_db_session
from .helpers import ensure_utc_timestamp

logger = logging.getLogger(__name__)

NEW_YORK = "America/New_York"
EARNINGS_LIMIT = 40  # ~10 years of quarterly reports: enough for a historical RMS move
DIVIDEND_YEARS = 10
MAX_STALE_DAYS = 3  # a Friday refresh still covers Monday's runs
SYMBOL_DELAY_SECONDS = 0.3


# ------------------------------------------------------------------
# Timestamps
# ------------------------------------------------------------------


def _new_york(ts) -> pd.Timestamp:
    """A yfinance (aware) or stored (naive UTC) timestamp in New York time."""
    t = pd.Timestamp(ts)
    return (t.tz_localize(UTC) if t.tzinfo is None else t).tz_convert(NEW_YORK)


def _naive_utc(ts) -> datetime:
    return ensure_utc_timestamp(pd.Timestamp(ts)).to_pydatetime().replace(tzinfo=None)


def report_after_close(ts) -> bool | None:
    """
    True if a report stamped `ts` comes after the close, False before the open,
    None if unknown. yfinance stamps an unknown time as bare midnight New York;
    anything at 12:00 New York or later is after the close.
    """
    local = _new_york(ts)
    if local.hour == 0 and local.minute == 0:
        return None
    return local.hour >= 12


def report_session_date(ts) -> date:
    """The New York calendar date of a report timestamp."""
    return _new_york(ts).date()


# ------------------------------------------------------------------
# Fetch (yfinance)
# ------------------------------------------------------------------


@dataclass(frozen=True)
class EarningsRecord:
    report_date: datetime  # naive UTC, time of day kept
    eps_estimate: float | None = None
    reported_eps: float | None = None
    surprise_pct: float | None = None
    after_close: bool | None = None


@dataclass(frozen=True)
class DividendRecord:
    ex_date: date
    amount: float
    source: str  # "history" (paid) or "calendar" (announced, amount = last paid)


def _num(v) -> float | None:
    with contextlib.suppress(TypeError, ValueError):
        return None if v is None or pd.isna(v) else float(v)
    return None


def parse_earnings_frame(df: pd.DataFrame | None) -> list[EarningsRecord]:
    """yfinance get_earnings_dates() -> records. Column names vary between versions."""
    if df is None or not len(df):
        return []
    out = []
    for ts, row in df.iterrows():
        if pd.isna(ts):
            continue
        fields: dict[str, float | None] = {}
        for k, v in row.to_dict().items():
            name = (k or "").lower()
            if "estimate" in name and "eps" in name:
                fields["eps_estimate"] = _num(v)
            elif "reported" in name and "eps" in name:
                fields["reported_eps"] = _num(v)
            elif "surprise" in name:
                fields["surprise_pct"] = _num(v)
        out.append(EarningsRecord(_naive_utc(ts), after_close=report_after_close(ts), **fields))
    return out


def fetch_earnings(symbol: str, limit: int = EARNINGS_LIMIT, today: date | None = None) -> list[EarningsRecord] | None:
    """
    Past and scheduled reports, or None if yfinance failed (distinct from [],
    which an ETF legitimately returns). If yfinance's earnings dates hold no
    future report, the calendar's next date is added with unknown timing.
    """
    today = today or datetime.now(UTC).date()
    ticker = yf.Ticker(symbol)
    try:
        records = parse_earnings_frame(ticker.get_earnings_dates(limit=limit))
    except Exception as e:
        logger.warning("Earnings dates for %s unavailable: %s", symbol, e)
        return None
    if not any(report_session_date(r.report_date) >= today for r in records):
        with contextlib.suppress(Exception):
            upcoming = sorted(
                d for d in (ticker.calendar or {}).get("Earnings Date") or [] if isinstance(d, date) and d >= today
            )
            if upcoming:
                midnight = pd.Timestamp(upcoming[0]).tz_localize(NEW_YORK)
                records.append(EarningsRecord(_naive_utc(midnight)))
    return records


def fetch_dividends(symbol: str, today: date | None = None) -> list[DividendRecord] | None:
    """Paid ex-dates of the last DIVIDEND_YEARS plus the next announced one; None if yfinance failed."""
    today = today or datetime.now(UTC).date()
    ticker = yf.Ticker(symbol)
    try:
        divs = ticker.dividends
    except Exception as e:
        logger.warning("Dividend history for %s unavailable: %s", symbol, e)
        return None
    cutoff = today - timedelta(days=365 * DIVIDEND_YEARS)
    out = {}
    if divs is not None:
        for ts, amount in divs.items():
            ex = report_session_date(ts)
            if ex >= cutoff and _num(amount):
                out[ex] = DividendRecord(ex, float(amount), "history")
    last_paid = out[max(out)].amount if out else None
    with contextlib.suppress(Exception):
        raw = (ticker.calendar or {}).get("Ex-Dividend Date")
        ex = pd.Timestamp(raw).date() if raw is not None else None
        if ex is not None and ex >= today and ex not in out and last_paid:
            out[ex] = DividendRecord(ex, last_paid, "calendar")
    return [out[d] for d in sorted(out)]


# ------------------------------------------------------------------
# Upsert
# ------------------------------------------------------------------


def upsert_earnings(session, symbol: str, records: list[EarningsRecord]) -> tuple[int, int]:
    """
    Insert new reports and fill in what existing ones lack. Returns (added, updated).

    Rows match on the report's New York date, not the exact timestamp: the
    calendar's midnight placeholder and the later timed stamp are the same
    report. A known time replaces an unknown one, and actual EPS and surprise
    fill in once reported. Rows stored before after_close existed get it
    derived from their own timestamp.
    """
    existing: dict[date, StockEarnings] = {}
    for row in session.query(StockEarnings).filter(StockEarnings.symbol == symbol).order_by(StockEarnings.id):
        existing.setdefault(report_session_date(row.report_date), row)
    added = updated = 0
    for rec in records:
        day = report_session_date(rec.report_date)
        row = existing.get(day)
        if row is None:
            row = StockEarnings(
                symbol=symbol,
                report_date=rec.report_date,
                eps_estimate=rec.eps_estimate,
                reported_eps=rec.reported_eps,
                surprise_pct=rec.surprise_pct,
                after_close=rec.after_close,
            )
            session.add(row)
            existing[day] = row
            added += 1
            continue
        changed = False
        if rec.after_close is not None and report_after_close(row.report_date) is None:
            row.report_date = rec.report_date
            changed = True
        for name in ("eps_estimate", "reported_eps", "surprise_pct"):
            value = getattr(rec, name)
            if value is not None and getattr(row, name) != value:
                setattr(row, name, value)
                changed = True
        timing = rec.after_close if rec.after_close is not None else report_after_close(row.report_date)
        if row.after_close != timing and timing is not None:
            row.after_close = timing
            changed = True
        updated += changed
    session.flush()
    return added, updated


def upsert_dividends(session, symbol: str, records: list[DividendRecord], today: date | None = None) -> tuple[int, int]:
    """
    Insert new ex-dates, let a paid amount replace an announced one, and drop
    an announced future date the calendar no longer lists (it moved). Returns
    (added, updated).
    """
    today = today or datetime.now(UTC).date()
    rows = {r.ex_date: r for r in session.query(DividendEvent).filter(DividendEvent.symbol == symbol)}
    fetched = {r.ex_date for r in records}
    added = updated = 0
    for rec in records:
        row = rows.get(rec.ex_date)
        if row is None:
            session.add(DividendEvent(symbol=symbol, ex_date=rec.ex_date, amount=rec.amount, source=rec.source))
            added += 1
        elif row.source != "history" and (row.amount != rec.amount or row.source != rec.source):
            row.amount, row.source = rec.amount, rec.source
            updated += 1
    for ex, row in rows.items():
        if row.source == "calendar" and ex >= today and ex not in fetched:
            session.delete(row)
            updated += 1
    session.flush()
    return added, updated


@dataclass
class RefreshStats:
    symbols: int = 0
    with_earnings: set[str] = field(default_factory=set)
    failed: list[str] = field(default_factory=list)
    earnings_added: int = 0
    earnings_updated: int = 0
    dividends_added: int = 0
    dividends_updated: int = 0


def refresh_corporate_events(symbols, today: date | None = None, delay: float = SYMBOL_DELAY_SECONDS) -> RefreshStats:
    """
    Fetch and upsert earnings and dividends for each symbol, one savepoint per
    symbol so a bad one loses only itself. A symbol is marked refreshed when
    at least one of the two fetches worked. An ETF has dividends but no
    earnings, and that is a real answer.
    """
    today = today or datetime.now(UTC).date()
    stats = RefreshStats()
    symbols = sorted(set(symbols))
    with get_db_session() as session:
        for i, symbol in enumerate(symbols):
            stats.symbols += 1
            try:
                earnings = fetch_earnings(symbol, today=today)
                dividends = fetch_dividends(symbol, today=today)
                if earnings is None and dividends is None:
                    stats.failed.append(symbol)
                    continue
                with session.begin_nested():
                    ea, eu = upsert_earnings(session, symbol, earnings or [])
                    da, du = upsert_dividends(session, symbol, dividends or [], today=today)
                    ref = session.get(CorporateEventRefresh, symbol) or CorporateEventRefresh(symbol=symbol)
                    ref.refreshed_at = datetime.now(UTC).replace(tzinfo=None)
                    ref.n_earnings = len(earnings or [])
                    ref.n_dividends = len(dividends or [])
                    session.add(ref)
                stats.earnings_added += ea
                stats.earnings_updated += eu
                stats.dividends_added += da
                stats.dividends_updated += du
                if earnings:
                    stats.with_earnings.add(symbol)
            except Exception as e:
                logger.warning("Corporate events for %s failed: %s", symbol, e, exc_info=True)
                stats.failed.append(symbol)
            if delay and i < len(symbols) - 1:
                time.sleep(delay)
    return stats


# ------------------------------------------------------------------
# Read
# ------------------------------------------------------------------


@dataclass(frozen=True)
class StoredEvents:
    earnings: list[tuple[date, bool | None]]  # (New York report date, after_close), oldest first
    dividends: list[tuple[date, float]]  # (ex-date, amount), oldest first
    refreshed_at: datetime


def stored_events(symbol: str, today: date | None = None, max_stale_days: int = MAX_STALE_DAYS) -> StoredEvents | None:
    """
    The stored events of `symbol` if it was refreshed within max_stale_days,
    else None (caller falls back to yfinance). A DB error also returns None.
    """
    today = today or datetime.now(UTC).date()
    try:
        with get_db_session() as session:
            ref = session.get(CorporateEventRefresh, symbol)
            if ref is None or ref.refreshed_at.date() < today - timedelta(days=max_stale_days):
                return None
            refreshed_at = ref.refreshed_at
            earnings_rows = (
                session.query(StockEarnings.report_date, StockEarnings.after_close)
                .filter(StockEarnings.symbol == symbol)
                .all()
            )
            dividend_rows = (
                session.query(DividendEvent.ex_date, DividendEvent.amount).filter(DividendEvent.symbol == symbol).all()
            )
    except Exception as e:
        logger.warning("Stored corporate events for %s unreadable: %s", symbol, e)
        return None
    earnings: dict[date, bool | None] = {}
    for ts, after in earnings_rows:
        day = report_session_date(ts)
        timing = after if after is not None else report_after_close(ts)
        if earnings.get(day) is None:
            earnings[day] = timing
    return StoredEvents(
        earnings=sorted(earnings.items()),
        dividends=sorted((d, float(a)) for d, a in dividend_rows),
        refreshed_at=refreshed_at,
    )
