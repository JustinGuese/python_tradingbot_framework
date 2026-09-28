"""
Read-only client for Kalshi's public market-data API (no key needed).

Three things about the API that this module exists to absorb (verified against
the live API on 2026-09-28):

- **Two tiers.** Markets settled before `GET /historical/cutoff` are gone from
  `/markets` and the live candle path (404) and live under `/historical/...`
  instead. `list_markets` reads both and tags each market with `_historical`, so
  `candles` knows which path to call.
- **Two candle schemas.** Live candles carry suffixed decimal strings
  (`price.close_dollars`, `volume_fp`, `open_interest_fp`); historical candles
  carry the same values under bare keys (`price.close`, `volume`,
  `open_interest`). Kalshi also renamed the market fields from integer cents to
  `*_dollars` strings, and a parser reading the old names silently gets None for
  everything -- `parse_candle` reads both and the capture fails loudly when a
  series comes back all-null.
- **Legacy markets have no strike fields.** `FED-22DEC-T4.75` returns
  `strike_type: null`; the strike only survives in the ticker suffix, which
  `market_strike` falls back to.

Candles: `period_interval` is 1, 60 or 1440 minutes and one request covers at
most 5000 candles. A daily candle ends at midnight US/Eastern.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx

logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
MAX_CANDLES = 5000
PAGE_LIMIT = 1000


class KalshiNotFound(Exception):
    """404: the market is on the other tier, or does not exist."""


def _to_float(value) -> float | None:
    """Kalshi decimal strings ("0.3200", "1261824.30") and numbers -> float; "" / None -> None."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_time(value: str | None) -> datetime | None:
    """ISO timestamp -> naive UTC (the DB convention)."""
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


class KalshiClient:
    """Thin GET wrapper with retry. Pass `client` to inject an httpx.Client (tests)."""

    def __init__(
        self,
        client: httpx.Client | None = None,
        retries: int = 3,
        backoff: float = 2.0,
        pause: float = 0.06,
    ):
        self._owned = client is None
        self.client = client or httpx.Client(base_url=BASE_URL, timeout=30)
        self.retries = retries
        self.backoff = backoff
        self.pause = pause  # ~16 req/s, under the basic tier's ~20 reads/s

    def close(self) -> None:
        if self._owned:
            self.client.close()

    def __enter__(self) -> KalshiClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def get(self, path: str, params: dict | None = None) -> dict:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self.client.get(path, params=params)
                if response.status_code == 404:
                    raise KalshiNotFound(f"{path} {params}")
                response.raise_for_status()
                if self.pause:
                    time.sleep(self.pause)
                return response.json()
            except KalshiNotFound:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                last = exc
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status is not None and 400 <= status < 500 and status != 429:
                    raise
            if attempt < self.retries:
                logger.warning("Kalshi GET %s attempt %d failed: %s", path, attempt + 1, last)
                time.sleep(self.backoff * (attempt + 1))
        raise RuntimeError(f"Kalshi GET {path} failed after {self.retries + 1} attempts: {last}")

    def _paged(self, path: str, params: dict) -> Iterator[dict]:
        cursor = ""
        while True:
            payload = self.get(path, {**params, "limit": PAGE_LIMIT, **({"cursor": cursor} if cursor else {})})
            yield from payload.get("markets", [])
            cursor = payload.get("cursor") or ""
            if not cursor:
                return

    def list_markets(self, series_ticker: str) -> list[dict]:
        """Every market of a series, historical tier first, deduped by ticker.

        Each dict gets `_historical: bool` so `candles` picks the right path.
        """
        seen: dict[str, dict] = {}
        for market in self._paged("/historical/markets", {"series_ticker": series_ticker}):
            seen[market["ticker"]] = {**market, "_historical": True}
        for market in self._paged("/markets", {"series_ticker": series_ticker}):
            seen.setdefault(market["ticker"], {**market, "_historical": False})
        return list(seen.values())

    def candles(
        self,
        series_ticker: str,
        market: dict,
        start_ts: int,
        end_ts: int,
        period_minutes: int = 1440,
    ) -> list[dict]:
        """Raw candles for one market over [start_ts, end_ts], chunked to <= 5000 per request."""
        ticker = market["ticker"]
        if market.get("_historical"):
            path = f"/historical/markets/{ticker}/candlesticks"
        else:
            path = f"/series/{series_ticker}/markets/{ticker}/candlesticks"
        span = MAX_CANDLES * period_minutes * 60
        out: list[dict] = []
        lo = start_ts
        while lo < end_ts:
            hi = min(end_ts, lo + span)
            params = {"start_ts": lo, "end_ts": hi, "period_interval": period_minutes}
            out.extend(self.get(path, params).get("candlesticks", []))
            lo = hi
        return out


def _price_field(block: dict | None, name: str) -> float | None:
    """`close` from a live (`close_dollars`) or historical (`close`) OHLC block."""
    if not block:
        return None
    value = block.get(f"{name}_dollars")
    if value is None:
        value = block.get(name)
    return _to_float(value)


def parse_candle(candle: dict) -> dict:
    """One candle (either schema) -> {observed_at, prob, bid, ask, volume, open_interest}.

    `prob` is the last trade's close; on a day without trades it falls back to
    the bid/ask mid, then to `previous` (the last trade before the candle).
    """
    price = candle.get("price") or {}
    bid = _price_field(candle.get("yes_bid"), "close")
    ask = _price_field(candle.get("yes_ask"), "close")
    prob = _price_field(price, "close")
    if prob is None and bid is not None and ask is not None and ask > 0:
        prob = (bid + ask) / 2
    if prob is None:
        prob = _price_field(price, "previous")
    volume = candle.get("volume_fp", candle.get("volume"))
    open_interest = candle.get("open_interest_fp", candle.get("open_interest"))
    return {
        "observed_at": datetime.fromtimestamp(int(candle["end_period_ts"]), UTC).replace(tzinfo=None),
        "prob": prob,
        "bid": bid,
        "ask": ask,
        "volume": _to_float(volume),
        "open_interest": _to_float(open_interest),
    }


_CONTRACT_TYPES = {
    "greater": "close_above",
    "greater_or_equal": "close_above",
    "less": "close_below",
    "less_or_equal": "close_below",
    "between": "range",
}
_SUFFIX_THRESHOLD = re.compile(r"-T(-?\d+(?:\.\d+)?)$")
_SUFFIX_BETWEEN = re.compile(r"-B(-?\d+(?:\.\d+)?)$")


def market_strike(market: dict) -> tuple[str, float | None, float | None, str | None]:
    """(contract_type, strike, strike_cap, label) for a market.

    Uses Kalshi's strike fields when present; legacy markets carry them only in
    the ticker (`-T4.50` = above 4.50, `-B7737` = a range centred there), and
    FEDDECISION-style outcomes (`-H0`, `-C25`) are event contracts whose outcome
    is the label.
    """
    ticker = market.get("ticker", "")
    strike_type = market.get("strike_type")
    floor = _to_float(market.get("floor_strike"))
    cap = _to_float(market.get("cap_strike"))
    label = market.get("yes_sub_title") or None
    if strike_type in _CONTRACT_TYPES:
        kind = _CONTRACT_TYPES[strike_type]
        if kind == "close_below":
            return kind, cap, None, label
        return kind, floor, cap, label
    if m := _SUFFIX_THRESHOLD.search(ticker):
        return "close_above", float(m.group(1)), None, label
    if m := _SUFFIX_BETWEEN.search(ticker):
        return "range", float(m.group(1)), None, label
    suffix = ticker.rsplit("-", 1)[-1] if "-" in ticker else None
    return "event", None, None, label or suffix


def market_meta(market: dict) -> dict:
    """The per-market columns of a PredictionMarketSnapshot row."""
    kind, strike, cap, label = market_strike(market)
    result = market.get("result") or None
    return {
        "event_ticker": market.get("event_ticker"),
        "market_ticker": market["ticker"],
        "label": label,
        "contract_type": kind,
        "strike": strike,
        "strike_cap": cap,
        "expiry": _parse_time(market.get("close_time")),
        "result": result if result in ("yes", "no") else None,
    }


def market_window(market: dict) -> tuple[datetime | None, datetime | None]:
    """(open_time, close_time) as naive UTC."""
    return _parse_time(market.get("open_time")), _parse_time(market.get("close_time"))
