"""Hyperliquid funding-rate carry, delta-neutral (long spot / short perp), study only.

PnL per hour = funding rate on the notional (shorts collect positive funding). Costs: one round trip of
spot + perp fees (default 15 bp) charged at entry and again whenever the book is re-entered (modelled as
a fixed annual turnover). Borrow is zero for spot-long/perp-short. Long-only spot leg, no leveraged shorts
beyond the perp hedge. Reported vs QQQ per the CLAUDE.md alpha method. Not a bot.
Run: python scripts/onetime_funding_carry_study.py
"""

from __future__ import annotations

import time

import httpx
import numpy as np
import pandas as pd
import yfinance as yf

from tradingbot.utils.alpha_report import alpha_stats

COINS = ["BTC", "ETH", "SOL"]
URL = "https://api.hyperliquid.xyz/info"
ROUND_TRIP_BP = 15.0
TURNOVER_PER_YEAR = 4.0  # re-entries per year charged against the book


def fetch(coin: str) -> pd.Series:
    out: list[dict] = []
    start = 1_640_000_000_000
    while True:
        for wait in (2, 5, 15, 30, 60):
            r = httpx.post(URL, json={"type": "fundingHistory", "coin": coin, "startTime": start}, timeout=30)
            if r.status_code not in (429, 500, 502, 503, 504):
                break
            time.sleep(wait)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break
        out += rows
        last = rows[-1]["time"]
        if len(rows) < 500 or last <= start:
            break
        start = last + 1
        time.sleep(1.0)
    s = pd.Series({pd.to_datetime(x["time"], unit="ms", utc=True): float(x["fundingRate"]) for x in out})
    return s.sort_index()


def run() -> None:
    funding = {c: fetch(c) for c in COINS}
    daily = pd.DataFrame({c: s.resample("D").sum() for c, s in funding.items()}).dropna()
    daily.index = daily.index.tz_localize(None)
    book = daily.mean(axis=1) - ROUND_TRIP_BP / 1e4 * TURNOVER_PER_YEAR / 365
    qqq = yf.download("QQQ", start=str(book.index[0].date()), auto_adjust=True, progress=False)["Close"].squeeze()
    q = qqq.pct_change().dropna()
    # 7-day funding series -> QQQ trading days: sum of carry since the previous trading day
    cum = book.cumsum().reindex(q.index.union(book.index)).ffill().reindex(q.index)
    r = cum.diff().dropna()
    mid = r.index[len(r) // 2]
    print(f"{COINS}, {r.index[0].date()} -> {r.index[-1].date()}, split {mid.date()}")
    print(f"gross funding yield {daily.mean(axis=1).mean() * 365:.2%}/yr, net {book.mean() * 365:.2%}/yr")
    for label, sl in (("H1", r.index < mid), ("H2", r.index >= mid), ("Full", slice(None))):
        s = alpha_stats(r[sl], q.reindex(r.index)[sl])
        print(label, "not measurable" if s is None else
              f"alpha {s.alpha:+.2%}/yr t {s.t:.2f} beta {s.beta:.2f} corr {s.corr:.2f} n {s.n}")
    eq = (1 + book).cumprod()
    print(f"max DD (funding only, ignores exchange/basis risk) {(eq / eq.cummax() - 1).min():.2%}")


if __name__ == "__main__":
    run()
