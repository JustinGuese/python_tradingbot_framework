"""
Daily capture of the curated prediction-market series into
`prediction_market_snapshots` (one row per market per US/Eastern day).

The daily run re-reads the last `lookback_days` of every market open in that
window; `backfill=True` reads every market's whole life. Both lean on the
repository's ON CONFLICT DO NOTHING, so re-running is harmless.

A row's `date` is the Eastern day the price covers. Kalshi's daily candle for
day D ends at midnight ET (D+1 00:00), which is when D's price is known;
Polymarket's daily point for D is sampled at 00:00 UTC of D+1 (20:00 ET on D).
Either way `date = ET date of (observed_at - 1 minute)`. A candle whose end is
still in the future is today's partial candle and is not stored.

Fail loudly: a series that returned candles/points whose prices all parsed to
None raises `AllNullPrices` — that is exactly what a renamed API field looks
like (Kalshi moved from integer cents to `*_dollars` strings), and a job that
stored nothing but exited 0 would hide it indefinitely.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from . import kalshi, polymarket
from .prediction_market_repository import bulk_insert_snapshots, record_results
from .prediction_market_series import SERIES, Series

logger = logging.getLogger(__name__)

EASTERN = ZoneInfo("America/New_York")
DEFAULT_LOOKBACK_DAYS = 7


class AllNullPrices(RuntimeError):
    """A series returned prices, and every one of them parsed to None."""


@dataclass
class CaptureResult:
    written: dict[str, int] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)

    @property
    def rows(self) -> int:
        return sum(self.written.values())


def covered_date(observed_at: datetime):
    """The US/Eastern day a price observed at `observed_at` (naive UTC) covers."""
    return (observed_at.replace(tzinfo=UTC) - timedelta(minutes=1)).astimezone(EASTERN).date()


def _rows(series: Series, meta: dict, prices: list[dict], now: datetime) -> list[dict]:
    out = []
    for price in prices:
        if price["prob"] is None or price["observed_at"] > now:
            continue
        out.append(
            {
                **meta,
                **price,
                "date": covered_date(price["observed_at"]),
                "venue": series.venue,
                "series": series.key,
            }
        )
    return out


class _Buffer:
    """Rows collected across markets and inserted in large batches: one
    transaction per market made a backfill of ~3k markets take hours over a
    port-forward."""

    def __init__(self, flush_at: int = 20_000):
        self.rows: list[dict] = []
        self.flush_at = flush_at
        self.written = 0

    def add(self, rows: list[dict]) -> None:
        self.rows.extend(rows)
        if len(self.rows) >= self.flush_at:
            self.flush()

    def flush(self) -> int:
        self.written += bulk_insert_snapshots(self.rows)
        self.rows = []
        return self.written


def _check_nulls(series: Series, n_prices: int, n_priced: int) -> None:
    if n_prices and not n_priced:
        raise AllNullPrices(f"{series.key}: {n_prices} prices fetched, every one parsed to None")


def capture_kalshi(
    series: Series,
    client: kalshi.KalshiClient,
    now: datetime,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    backfill: bool = False,
) -> int:
    since = now - timedelta(days=lookback_days)
    buffer = _Buffer()
    n_prices = n_priced = 0
    results: dict[str, str] = {}
    for source in series.sources:
        for market in client.list_markets(source):
            opened, closed = kalshi.market_window(market)
            meta = kalshi.market_meta(market)
            if meta["result"]:
                results[market["ticker"]] = meta["result"]
            if opened is None or opened > now:
                continue
            if not backfill and closed is not None and closed < since:
                continue
            start = opened if backfill else max(opened, since)
            end = min(closed, now) if closed else now
            if end <= start:
                continue
            candles = client.candles(
                source,
                market,
                int(start.replace(tzinfo=UTC).timestamp()),
                # the candle covering the close day ends at the next midnight ET
                int((end + timedelta(days=1)).replace(tzinfo=UTC).timestamp()),
            )
            prices = [kalshi.parse_candle(c) for c in candles]
            n_prices += len(prices)
            n_priced += sum(p["prob"] is not None for p in prices)
            buffer.add(_rows(series, meta, prices, now))
    written = buffer.flush()
    _check_nulls(series, n_prices, n_priced)
    record_results(series.venue, results)
    return written


def capture_polymarket(
    series: Series,
    client: polymarket.PolymarketClient,
    now: datetime,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    backfill: bool = False,
) -> int:
    since = now - timedelta(days=lookback_days)
    buffer = _Buffer()
    n_prices = n_priced = 0
    results: dict[str, str] = {}
    for slug in series.sources:
        event = client.event(slug)
        for market in event.get("markets", []):
            meta = polymarket.market_meta(market, slug)
            if meta["result"]:
                results[meta["market_ticker"]] = meta["result"]
            if not backfill and meta["expiry"] is not None and meta["expiry"] < since and market.get("closed"):
                continue
            token = polymarket.yes_token(market)
            if not token:
                continue
            prices = [polymarket.parse_point(p) for p in client.price_history(token)]
            if not backfill:
                prices = [p for p in prices if p["observed_at"] >= since]
            n_prices += len(prices)
            n_priced += sum(p["prob"] is not None for p in prices)
            buffer.add(_rows(series, meta, prices, now))
    written = buffer.flush()
    _check_nulls(series, n_prices, n_priced)
    record_results(series.venue, results)
    return written


def capture_all(
    series: tuple[Series, ...] = SERIES,
    backfill: bool = False,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    now: datetime | None = None,
    kalshi_client: kalshi.KalshiClient | None = None,
    polymarket_client: polymarket.PolymarketClient | None = None,
) -> CaptureResult:
    """Capture every series; one failing series is recorded and the rest still run."""
    now = now or datetime.now(UTC).replace(tzinfo=None)
    kc = kalshi_client or kalshi.KalshiClient()
    pc = polymarket_client or polymarket.PolymarketClient()
    result = CaptureResult()
    try:
        for s in series:
            try:
                if s.venue == "kalshi":
                    n = capture_kalshi(s, kc, now, lookback_days, backfill)
                else:
                    n = capture_polymarket(s, pc, now, lookback_days, backfill)
                result.written[s.key] = n
                logger.info("prediction markets: %s -> %d new rows", s.key, n)
            except Exception as exc:
                logger.exception("prediction markets: %s failed", s.key)
                result.failed[s.key] = f"{type(exc).__name__}: {exc}"
    finally:
        if kalshi_client is None:
            kc.close()
        if polymarket_client is None:
            pc.close()
    return result
