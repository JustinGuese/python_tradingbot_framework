"""
Historical option chains from the DoltHub `post-no-preference/options` mirror.

yfinance serves no historical chains, and our own capture
(utils/option_capture.py) only started on 2026-09-26 — every option-bot
backtest before this was priced off a Black-Scholes/IV-proxy model
(scripts/onetime_option_bots_backtest.py), never an observed chain.

`post-no-preference/options` is a free, community-maintained Dolt database
covering ~2,000+ US names. Queried with no API key over a plain HTTP SQL
endpoint (branch `master`, not `main`). Live-checked on 2026-09-28: coverage
runs from 2019-02-09 through at least 2026-09-25 for AAPL and SPY — this is a
maintained mirror, not the stale 2024-11 dump some write-ups describe.

Schema of `option_chain`: date, act_symbol, expiration, strike, call_put
("Call"/"Put"), bid, ask, vol (IV), delta, gamma, theta, vega, rho. There is no
open_interest, volume, last_price or underlying spot price column — those are
stored NULL on the mapped row; underlying_price is filled in by the caller
from a separate daily-close series (see scripts/onetime_backfill_dolthub_options.py).

The hosted endpoint times out (~context deadline exceeded) on unindexed scans
(date BETWEEN ranges, aggregates with no act_symbol filter) but answers a
`date IN (...)` batch for one symbol in a few seconds per day of history, so
`fetch_chain_days` batches trading days per query rather than pulling a range.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from datetime import date, datetime
from datetime import time as dtime

import requests  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

API_URL = "https://www.dolthub.com/api/v1alpha1/post-no-preference/options/master"
EARLIEST_DATE = date(2019, 2, 9)
# EOD snapshot time for backfilled rows: after the close, so it can never
# collide with (and never overwrites) a same-day intraday live capture, which
# runs at 19:45 UTC.
BACKFILL_SNAPSHOT_TIME = dtime(21, 0)


class DoltHubQueryError(RuntimeError):
    """A DoltHub query failed on every retry."""


class DoltHubRowLimitError(DoltHubQueryError):
    """The hosted endpoint refused the query for returning too many rows.

    Seen on batches spanning a high-volatility stretch (e.g. 15 trading days
    across March 2020), where the listed-strikes-and-expiries count is much
    higher than a quiet period's. Retrying the identical query never helps —
    only asking for fewer dates does — so this is raised immediately, with no
    retry/backoff, so the caller can split the batch instead of burning ~30s
    per failed attempt on a query that will never succeed.
    """


def _run_query(sql: str, timeout: float = 60.0, retries: int = 3, backoff: float = 5.0) -> list[dict]:
    last_err: Exception | str = "unknown error"
    for attempt in range(retries + 1):
        try:
            resp = requests.get(API_URL, params={"q": sql}, timeout=timeout)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:  # network error, non-2xx, bad JSON
            last_err = e
        else:
            if payload.get("query_execution_status") == "Success":
                return payload.get("rows", [])
            last_err = payload.get("query_execution_message") or payload.get("query_execution_status")
            if last_err == "RowLimit":
                raise DoltHubRowLimitError(f"DoltHub RowLimit\nSQL: {sql}")
        if attempt < retries:
            logger.warning("DoltHub query attempt %d/%d failed: %s", attempt + 1, retries + 1, last_err)
            time.sleep(backoff * (attempt + 1))
    raise DoltHubQueryError(f"DoltHub query failed after {retries + 1} attempts: {last_err}\nSQL: {sql}")


def _fetch_batch(symbol: str, batch: Sequence[date]) -> list[dict]:
    """One DoltHub query for `batch`, halving and recursing on RowLimit until it fits.

    Bottoms out at single-day queries -- if even one day alone overflows the
    limit, that is a real DoltHub-side issue (not something splitting can fix)
    and the RowLimit error is left to propagate.
    """
    date_list = ", ".join(f"'{d.isoformat()}'" for d in batch)
    sql = f"SELECT * FROM option_chain WHERE act_symbol = '{symbol}' AND date IN ({date_list})"
    try:
        return _run_query(sql)
    except DoltHubRowLimitError:
        if len(batch) <= 1:
            raise
        mid = len(batch) // 2
        logger.info("DoltHub RowLimit on %d days for %s; splitting %d/%d", len(batch), symbol, mid, len(batch) - mid)
        return _fetch_batch(symbol, batch[:mid]) + _fetch_batch(symbol, batch[mid:])


def fetch_chain_days(
    symbol: str,
    trading_days: Sequence[date],
    batch_size: int = 15,
    pause: float = 0.0,
) -> list[dict]:
    """Raw `option_chain` rows for `symbol` on each of `trading_days`.

    Batched (default 15 trading days per request, about 3 weeks) to stay under
    the hosted endpoint's query timeout — a single unbatched multi-year range
    scan reliably times out. A batch that still trips the server's row cap
    (busy stretches return far more rows than quiet ones) is halved and retried
    rather than failing the whole run. Returns dicts with DoltHub's raw column
    names (act_symbol, call_put, vol, ...), unmodified; map with
    `to_option_quote_rows`.
    """
    out: list[dict] = []
    days = sorted(set(trading_days))
    for i in range(0, len(days), batch_size):
        batch = days[i : i + batch_size]
        out.extend(_fetch_batch(symbol, batch))
        if pause:
            time.sleep(pause)
    return out


def occ_symbol(root: str, expiration: date, right: str, strike: float) -> str:
    """Build an OCC contract symbol matching options.OCC_RE, e.g. AAPL251017C00150000."""
    return f"{root}{expiration:%y%m%d}{right}{round(strike * 1000):08d}"


def to_option_quote_rows(raw_rows: list[dict], underlying_prices: dict[date, float]) -> list[dict]:
    """Map raw DoltHub `option_chain` rows to OptionQuote-shaped dicts.

    `underlying_prices` maps trading day -> the underlying's close that day
    (from a separate daily OHLCV fetch — DoltHub has no spot price column).
    A row whose date is missing from `underlying_prices` still comes through,
    just with `underlying_price=None` (moneyness/IV-vs-spot analysis on that
    row loses precision, but the bid/ask/IV/greeks are not discarded for it).
    """
    out = []
    for r in raw_rows:
        d = date.fromisoformat(r["date"])
        expiration = date.fromisoformat(r["expiration"])
        right = "C" if r["call_put"] == "Call" else "P"
        strike = float(r["strike"])
        out.append(
            {
                "underlying": r["act_symbol"],
                "contract_symbol": occ_symbol(r["act_symbol"], expiration, right, strike),
                "expiration": datetime.combine(expiration, dtime.min),
                "option_type": right,
                "strike": strike,
                "bid": float(r["bid"]) if r.get("bid") is not None else None,
                "ask": float(r["ask"]) if r.get("ask") is not None else None,
                "last_price": None,
                "volume": None,
                "open_interest": None,
                "implied_volatility": float(r["vol"]) if r.get("vol") is not None else None,
                "underlying_price": underlying_prices.get(d),
                "snapshot_at": datetime.combine(d, BACKFILL_SNAPSHOT_TIME),
            }
        )
    return out
