"""
Read-only client for Polymarket's public APIs (Gamma for markets, CLOB for prices).

Verified against the live API on 2026-09-28:

- Gamma's `outcomes`, `outcomePrices` and `clobTokenIds` are JSON-encoded
  *strings* inside the JSON, so they need a second `json.loads`.
- CLOB `prices-history?market=<token>&interval=max&fidelity=1440` returns the
  whole daily history in one call. The 15-day window limit only applies when
  both `startTs` and `endTs` are passed, so this module never passes them.
- Points are `{"t": unix seconds, "p": float}`; `t` is the moment the price was
  sampled. Polymarket geo-blocks trading, not reading.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime

import httpx

logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"


def _json_list(value) -> list:
    if isinstance(value, list):
        return value
    if not value:
        return []
    return json.loads(value)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    if text.endswith("+00"):  # closedTime: "2024-12-18 22:24:42+00"
        text += ":00"
    parsed = datetime.fromisoformat(text)
    return parsed.astimezone(UTC).replace(tzinfo=None) if parsed.tzinfo else parsed


class PolymarketClient:
    def __init__(self, client: httpx.Client | None = None, retries: int = 3, backoff: float = 2.0):
        self._owned = client is None
        self.client = client or httpx.Client(timeout=30)
        self.retries = retries
        self.backoff = backoff

    def close(self) -> None:
        if self._owned:
            self.client.close()

    def __enter__(self) -> PolymarketClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def get(self, url: str, params: dict | None = None):
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self.client.get(url, params=params)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                last = exc
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status is not None and 400 <= status < 500 and status != 429:
                    raise
            if attempt < self.retries:
                logger.warning("Polymarket GET %s attempt %d failed: %s", url, attempt + 1, last)
                time.sleep(self.backoff * (attempt + 1))
        raise RuntimeError(f"Polymarket GET {url} failed after {self.retries + 1} attempts: {last}")

    def event(self, slug: str) -> dict:
        """The Gamma event for `slug`, with its markets. Raises if unknown."""
        events = self.get(f"{GAMMA_URL}/events", {"slug": slug})
        if not events:
            raise LookupError(f"Polymarket event {slug!r} not found")
        return events[0]

    def price_history(self, token_id: str) -> list[dict]:
        """Daily {t, p} points for one outcome token, from market start to now."""
        payload = self.get(f"{CLOB_URL}/prices-history", {"market": token_id, "interval": "max", "fidelity": 1440})
        return payload.get("history", [])


def yes_token(market: dict) -> str | None:
    """The CLOB token id of the market's "Yes" outcome (the first one if unnamed)."""
    outcomes = [str(o).lower() for o in _json_list(market.get("outcomes"))]
    tokens = _json_list(market.get("clobTokenIds"))
    if not tokens:
        return None
    return tokens[outcomes.index("yes")] if "yes" in outcomes else tokens[0]


def market_result(market: dict) -> str | None:
    """ "yes" / "no" for a resolved market, else None."""
    if not market.get("closed") or market.get("umaResolutionStatus") != "resolved":
        return None
    prices = [float(p) for p in _json_list(market.get("outcomePrices"))]
    outcomes = [str(o).lower() for o in _json_list(market.get("outcomes"))]
    if not prices or "yes" not in outcomes:
        return None
    return "yes" if prices[outcomes.index("yes")] >= 0.5 else "no"


def market_meta(market: dict, event_slug: str) -> dict:
    return {
        "event_ticker": event_slug,
        "market_ticker": market["slug"],
        "label": market.get("groupItemTitle") or market.get("question"),
        "contract_type": "event",
        "strike": None,
        "strike_cap": None,
        "expiry": _parse_time(market.get("endDate")),
        "result": market_result(market),
    }


def parse_point(point: dict) -> dict:
    return {
        "observed_at": datetime.fromtimestamp(int(point["t"]), UTC).replace(tzinfo=None),
        "prob": float(point["p"]) if point.get("p") is not None else None,
        "bid": None,
        "ask": None,
        "volume": None,
        "open_interest": None,
    }
