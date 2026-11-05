"""
World-event latency logger (long-running Deployment, not a CronJob).

Polls RSS/Atom feeds, Alpaca news and the `telegram_messages` table, stores every
sighting in `world_events`, and prices a watchlist at +0/1/5/15/60 min after each
first sighting into `event_price_snapshots`. It never trades. See
utils/world_events.py and docs in AGENTS.md.

Env:
  EVENT_RSS_FEEDS        "name=url,name2=url2" (default: utils.world_events.DEFAULT_RSS_FEEDS)
  EVENT_WATCHLIST        comma-separated yfinance symbols (default: DEFAULT_WATCHLIST)
  EVENT_RSS_INTERVAL_S   default 45
  EVENT_ALPACA_INTERVAL_S default 15   (REST poll; needs ALPACA_API_KEY / ALPACA_API_SECRET, else skipped)
  EVENT_SNAPSHOT_INTERVAL_S default 10

Alpaca is polled over REST, so its floor is the poll interval. If the study shows
Alpaca is the fast source, swap in its news websocket (adds a `websockets` dependency).
"""

import asyncio
import logging
import os
import sys

import httpx

from tradingbot.utils.config import setup_logging
from tradingbot.utils.db import init_db
from tradingbot.utils.world_events import (
    DEFAULT_RSS_FEEDS,
    DEFAULT_WATCHLIST,
    max_telegram_id,
    parse_alpaca_news,
    parse_feed,
    run_snapshot_pass,
    store_events,
    telegram_events,
    utcnow_naive,
)

logger = logging.getLogger(__name__)
USER_AGENT = "tradingbot-eventlogger/1.0 (research; contact via repo owner)"  # SEC requires an identifying UA
ALPACA_NEWS_URL = "https://data.alpaca.markets/v1beta1/news"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def rss_feeds() -> dict[str, str]:
    raw = os.environ.get("EVENT_RSS_FEEDS", "").strip()
    if not raw:
        return dict(DEFAULT_RSS_FEEDS)
    return dict(p.split("=", 1) for p in raw.split(",") if "=" in p)


def watchlist() -> list[str]:
    raw = os.environ.get("EVENT_WATCHLIST", "").strip()
    return [s.strip() for s in raw.split(",") if s.strip()] if raw else list(DEFAULT_WATCHLIST)


def _stamp(events):
    """Receipt time is taken as soon as the response is parsed, before any DB work."""
    now = utcnow_naive()
    return [e.__class__(**{**e.__dict__, "received_at": now}) for e in events]


async def poll_rss(client: httpx.AsyncClient, interval: float) -> None:
    feeds = rss_feeds()
    while True:
        for name, url in feeds.items():
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                events = _stamp(parse_feed(name, resp.text))
                new = await asyncio.to_thread(store_events, events)
                if new:
                    logger.info("rss:%s: %d new of %d items", name, len(new), len(events))
            except Exception as exc:  # one dead feed must not stop the others
                logger.warning("rss:%s failed: %s: %s", name, type(exc).__name__, exc)
        await asyncio.sleep(interval)


async def poll_alpaca(client: httpx.AsyncClient, interval: float) -> None:
    key, secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_API_SECRET")
    if not (key and secret):
        logger.info("alpaca: ALPACA_API_KEY/ALPACA_API_SECRET not set, source disabled")
        return
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    while True:
        try:
            resp = await client.get(ALPACA_NEWS_URL, params={"limit": 50, "sort": "desc"}, headers=headers)
            resp.raise_for_status()
            events = _stamp(parse_alpaca_news(resp.json().get("news", [])))
            new = await asyncio.to_thread(store_events, events)
            if new:
                logger.info("alpaca: %d new of %d items", len(new), len(events))
        except Exception as exc:
            logger.warning("alpaca failed: %s: %s", type(exc).__name__, exc)
        await asyncio.sleep(interval)


async def poll_telegram(interval: float = 5.0) -> None:
    """Bridge the existing telegram monitor's table. Starts at the current max id so a restart
    does not replay history; the unique key makes a replay harmless anyway."""
    last = await asyncio.to_thread(max_telegram_id)
    while True:
        try:
            rows = await asyncio.to_thread(telegram_events, last)
            if rows:
                events = [e for _id, e in rows if e is not None]
                new = await asyncio.to_thread(store_events, events)
                last = rows[-1][0]
                if new:
                    logger.info("telegram: %d new", len(new))
        except Exception as exc:
            logger.warning("telegram bridge failed: %s: %s", type(exc).__name__, exc)
        await asyncio.sleep(interval)


async def snapshot_loop(interval: float) -> None:
    symbols = watchlist()
    while True:
        try:
            n = await asyncio.to_thread(run_snapshot_pass, symbols)
            if n:
                logger.info("snapshots: %d rows", n)
        except Exception as exc:
            logger.warning("snapshot pass failed: %s: %s", type(exc).__name__, exc)
        await asyncio.sleep(interval)


async def main_async() -> None:
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        await asyncio.gather(
            poll_rss(client, _env_float("EVENT_RSS_INTERVAL_S", 45)),
            poll_alpaca(client, _env_float("EVENT_ALPACA_INTERVAL_S", 15)),
            poll_telegram(),
            snapshot_loop(_env_float("EVENT_SNAPSHOT_INTERVAL_S", 10)),
        )


def main() -> int:
    setup_logging()
    init_db()
    logger.info("eventlogger starting: %d feeds, %d watchlist symbols", len(rss_feeds()), len(watchlist()))
    asyncio.run(main_async())
    return 0


if __name__ == "__main__":
    sys.exit(main())
