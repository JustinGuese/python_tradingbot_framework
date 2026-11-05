"""
World-event latency logger: sources, dedupe, storage and price snapshots.

Step 1 of the event-driven plan. This module only RECORDS: it never trades and
never calls an LLM. The goal is to measure (a) how late each source is versus the
publisher's own timestamp, (b) which source sees a story first, and (c) how prices
of a fixed watchlist move 0/1/5/15/60 minutes after the first sighting. Those
three numbers decide whether any event class is worth a trading bot.

Pure helpers (parsing, dedupe key, due-offset logic) are separate from the DB and
network code so they are unit-testable. The long-running loop is in
tradingbot/eventlogger.py.
"""

from __future__ import annotations

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

from sqlalchemy.dialects import postgresql, sqlite

from .db import EventPriceSnapshot, TelegramMessage, WorldEvent, get_db_session

logger = logging.getLogger(__name__)

# Seconds after the first sighting at which every watchlist symbol is priced.
SNAPSHOT_OFFSETS: tuple[int, ...] = (0, 60, 300, 900, 3600)
# A snapshot later than this past its due time is skipped (process was down): a price
# read 2h late is not "+5 min".
SNAPSHOT_GRACE_S = 120

# Candidate watchlist: first-order and second-order assets from the plan. Override with
# EVENT_WATCHLIST. Keep it short; one batched yfinance call prices all of it.
DEFAULT_WATCHLIST: tuple[str, ...] = (
    "SPY",
    "QQQ",
    "GLD",
    "TLT",
    "UUP",  # macro
    "CL=F",
    "BZ=F",
    "NG=F",
    "USO",
    "XLE",
    "XOP",
    "CVX",  # energy
    "FRO",
    "STNG",
    "ZIM",  # tankers / container shipping
    "VLO",
    "MPC",  # refiners
    "JETS",
    "DAL",  # airlines
    "ITA",
    "LMT",  # defence
    "ZW=F",
    "ZC=F",
    "CF",
    "MOS",  # grains, fertilizer
    "CIBR",
    "KRE",
    "EEM",
    "BTC-USD",  # cyber, banks, EM, crypto risk-off
)

# Defaults are well-known public feeds but were NOT verified live from the build
# sandbox; the logger records per-feed errors, so a dead URL shows up in the log.
# Override with EVENT_RSS_FEEDS="name=url,name2=url2".
DEFAULT_RSS_FEEDS: dict[str, str] = {
    "fed": "https://www.federalreserve.gov/feeds/press_all.xml",
    "usgs": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_hour.atom",
    "nhc": "https://www.nhc.noaa.gov/index-at.xml",
    "bbc": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "sec8k": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&output=atom",
}


@dataclass(frozen=True)
class RawEvent:
    source: str
    external_id: str
    title: str
    body: str | None = None
    url: str | None = None
    symbols: str | None = None
    source_ts: datetime | None = None  # naive UTC
    received_at: datetime | None = None  # naive UTC, filled by the poller


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def to_naive_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt  # assume the caller already means UTC
    return dt.astimezone(UTC).replace(tzinfo=None)


def parse_ts(value: str | None) -> datetime | None:
    """RFC 822 (RSS) or ISO 8601 (Atom, Alpaca) to naive UTC; None if unparseable."""
    if not value:
        return None
    value = value.strip()
    try:
        return to_naive_utc(parsedate_to_datetime(value))
    except (TypeError, ValueError):
        pass
    try:
        return to_naive_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


_WS = re.compile(r"\s+")
_NOISE = re.compile(r"[^a-z0-9 ]")


def dedupe_key(title: str) -> str:
    """Stable key for 'the same story': lowercase, punctuation and whitespace stripped.
    Deliberately crude: two sources that reword a headline get two keys, which only
    costs duplicate snapshots, never a lost event."""
    norm = _WS.sub(" ", _NOISE.sub(" ", title.lower())).strip()
    return hashlib.sha1(norm.encode()).hexdigest()[:16]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_feed(source: str, xml_text: str) -> list[RawEvent]:
    """Parse an RSS 2.0 or Atom document into events. Items without a title are dropped."""
    root = ET.fromstring(xml_text)
    out: list[RawEvent] = []
    for item in root.iter():
        if _local(item.tag) not in ("item", "entry"):
            continue
        fields: dict[str, str] = {}
        link = None
        for child in item:
            name = _local(child.tag)
            if name == "link":
                link = child.get("href") or (child.text or "").strip() or link
            elif child.text and name not in fields:
                fields[name] = child.text.strip()
        title = fields.get("title")
        if not title:
            continue
        ts = parse_ts(fields.get("pubDate") or fields.get("published") or fields.get("updated") or fields.get("date"))
        ext = fields.get("guid") or fields.get("id") or link or title
        out.append(
            RawEvent(
                source=f"rss:{source}",
                external_id=ext,
                title=title,
                body=fields.get("description") or fields.get("summary"),
                url=link,
                source_ts=ts,
            )
        )
    return out


def parse_alpaca_news(items: list[dict]) -> list[RawEvent]:
    """Alpaca /v1beta1/news payload items (Benzinga headlines) to events."""
    out = []
    for it in items:
        title = it.get("headline")
        if not title or it.get("id") is None:
            continue
        out.append(
            RawEvent(
                source="alpaca",
                external_id=str(it["id"]),
                title=title,
                body=it.get("summary") or None,
                url=it.get("url"),
                symbols=",".join(it.get("symbols") or []) or None,
                source_ts=parse_ts(it.get("created_at")),
            )
        )
    return out


def due_offsets(received_at: datetime, now: datetime, done: set[int]) -> list[int]:
    """Offsets whose time has come, that are not stale, and that are not yet stored."""
    due = []
    for off in SNAPSHOT_OFFSETS:
        if off in done:
            continue
        lateness = (now - (received_at + timedelta(seconds=off))).total_seconds()
        if 0 <= lateness <= SNAPSHOT_GRACE_S:
            due.append(off)
    return due


def latest_prices(symbols: list[str]) -> dict[str, tuple[float, datetime]]:
    """Last 1-minute close and its bar time (naive UTC) per symbol. Symbols with no bar are omitted."""
    import yfinance as yf

    if not symbols:
        return {}
    df = yf.download(symbols, period="1d", interval="1m", progress=False, threads=True, auto_adjust=False)
    out: dict[str, tuple[float, datetime]] = {}
    if df is None or df.empty:
        return out
    close = df["Close"]
    if getattr(close, "ndim", 1) == 1:  # a single symbol comes back as a Series
        close = close.to_frame(symbols[0])
    for sym in close.columns:
        col = close[sym].dropna()
        if col.empty:
            continue
        ts = col.index[-1].to_pydatetime()
        out[str(sym)] = (float(col.iloc[-1]), to_naive_utc(ts))
    return out


def _insert_fn(session):
    return sqlite.insert if session.get_bind().dialect.name == "sqlite" else postgresql.insert


def store_events(events: list[RawEvent]) -> list[int]:
    """Insert new sightings (idempotent on source+external_id). Returns ids of the inserted rows.

    `is_first` is decided here: True when no row with the same dedupe_key was received in
    the previous 24h (rows from the same batch count, in order)."""
    new_ids: list[int] = []
    with get_db_session() as session:
        insert = _insert_fn(session)
        for ev in events:
            key = dedupe_key(ev.title)
            received = ev.received_at or utcnow_naive()
            seen = (
                session.query(WorldEvent.id)
                .filter(WorldEvent.dedupe_key == key, WorldEvent.received_at >= received - timedelta(hours=24))
                .first()
            )
            stmt = (
                insert(WorldEvent)
                .values(
                    source=ev.source,
                    external_id=ev.external_id[:500],
                    dedupe_key=key,
                    title=ev.title[:1000],
                    body=(ev.body or None) and ev.body[:4000],
                    url=ev.url,
                    symbols=ev.symbols,
                    source_ts=ev.source_ts,
                    received_at=received,
                    is_first=seen is None,
                    acted_on=False,
                )
                .on_conflict_do_nothing(index_elements=["source", "external_id"])
                .returning(WorldEvent.id)
            )
            row = session.execute(stmt).fetchone()
            if row:
                new_ids.append(row[0])
    return new_ids


def telegram_events(since_id: int) -> list[tuple[int, RawEvent]]:
    """New `telegram_messages` rows as events. `received_at` is the row's created_at: the
    monitor is a CronJob, so that is when WE saw it, and the gap to published_at is the point."""
    out = []
    with get_db_session() as session:
        rows = (
            session.query(TelegramMessage)
            .filter(TelegramMessage.id > since_id)
            .order_by(TelegramMessage.id)
            .limit(500)
            .all()
        )
        for r in rows:
            text = (r.text or r.summary or "").strip()
            if not text:
                out.append((r.id, None))
                continue
            out.append(
                (
                    r.id,
                    RawEvent(
                        source=f"telegram:{r.channel}",
                        external_id=str(r.message_id),
                        title=text.splitlines()[0][:300],
                        body=text[:4000],
                        symbols=r.symbol,
                        source_ts=to_naive_utc(r.published_at),
                        received_at=to_naive_utc(r.created_at),
                    ),
                )
            )
    return out


def max_telegram_id() -> int:
    with get_db_session() as session:
        return session.query(TelegramMessage.id).order_by(TelegramMessage.id.desc()).limit(1).scalar() or 0


def pending_snapshots(now: datetime) -> list[tuple[int, datetime, list[int]]]:
    """First-sighting events inside the snapshot window with at least one due offset."""
    horizon = now - timedelta(seconds=max(SNAPSHOT_OFFSETS) + SNAPSHOT_GRACE_S)
    out = []
    with get_db_session() as session:
        events = (
            session.query(WorldEvent.id, WorldEvent.received_at)
            .filter(WorldEvent.is_first.is_(True), WorldEvent.received_at >= horizon)
            .all()
        )
        for ev_id, received in events:
            done = {o for (o,) in session.query(EventPriceSnapshot.offset_s).filter_by(event_id=ev_id).distinct()}
            offs = due_offsets(received, now, done)
            if offs:
                out.append((ev_id, received, offs))
    return out


def store_snapshots(
    pairs: list[tuple[int, int]], prices: dict[str, tuple[float, datetime]], symbols: list[str], fetched_at: datetime
) -> int:
    """One row per (event, offset, symbol). A symbol with no bar is stored with NULL price so a
    gap is visible rather than silently absent. Returns rows inserted."""
    inserted = 0
    with get_db_session() as session:
        insert = _insert_fn(session)
        for ev_id, off in pairs:
            rows = []
            for sym in symbols:
                price, bar_ts = prices.get(sym, (None, None))
                rows.append(
                    {
                        "event_id": ev_id,
                        "offset_s": off,
                        "symbol": sym,
                        "price": price,
                        "bar_ts": bar_ts,
                        "fetched_at": fetched_at,
                    }
                )
            stmt = (
                insert(EventPriceSnapshot)
                .values(rows)
                .on_conflict_do_nothing(index_elements=["event_id", "offset_s", "symbol"])
                .returning(EventPriceSnapshot.id)
            )
            inserted += len(session.execute(stmt).fetchall())
    return inserted


def run_snapshot_pass(symbols: list[str], now: datetime | None = None) -> int:
    """Price the watchlist once and store every due (event, offset). One fetch serves all pairs."""
    now = now or utcnow_naive()
    pending = pending_snapshots(now)
    if not pending:
        return 0
    pairs = [(ev_id, off) for ev_id, _received, offs in pending for off in offs]
    prices = latest_prices(symbols)
    if not prices:
        logger.warning("snapshot pass: no prices returned for %d symbols (market closed or fetch failed)", len(symbols))
    return store_snapshots(pairs, prices, symbols, utcnow_naive())
