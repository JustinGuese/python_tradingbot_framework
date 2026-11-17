"""Long/flat 12-1 trend on instruments a Darwinex-style venue offers (FX, indices, commodities).

No shorts, no fitted parameters. Monthly rebalance, inverse-vol weights among assets with a positive
12-1 return, gross exposure <= 1 (idle cash earns 0 here). Signals use data up to t-1.
Alpha vs QQQ per the CLAUDE.md method, reported for each half and the full sample.
Run: python scripts/onetime_trend_darwinex_study.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import yfinance as yf

from tradingbot.utils.alpha_report import alpha_stats

ASSETS = [
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X",
    "^GSPC", "^GDAXI", "^N225", "^FTSE",
    "GC=F", "SI=F", "CL=F", "HG=F", "NG=F",
]  # fmt: skip
COST = 0.0005  # per unit of turnover
START = "2008-01-01"


def load() -> pd.DataFrame:
    px = yf.download(ASSETS + ["QQQ"], start=START, auto_adjust=True, progress=False)["Close"]
    return px.dropna(subset=["QQQ"]).ffill(limit=3)


def build_weights(px: pd.DataFrame) -> pd.DataFrame:
    a = px[ASSETS]
    mom = a.shift(21) / a.shift(252) - 1.0  # 12-1
    vol = a.pct_change().rolling(60).std() * np.sqrt(252)
    on = (mom > 0).astype(float)
    raw = on / vol
    w = raw.div(raw.sum(axis=1), axis=0).fillna(0.0)
    # gross <= 1: scale by 10% vol target per active name's share, never levering up
    gross = (on.sum(axis=1) / len(ASSETS)).clip(upper=1.0)
    w = w.mul(gross, axis=0)
    month_end = w.groupby(w.index.to_period("M")).tail(1).index
    wm = w.loc[month_end].reindex(w.index).ffill()
    return wm.shift(1).fillna(0.0)  # trade the day after the signal


def run() -> None:
    px = load()
    w = build_weights(px)
    rets = px[ASSETS].pct_change().fillna(0.0)
    gross = (w.shift(1).fillna(0.0) * rets).sum(axis=1)
    cost = w.diff().abs().sum(axis=1).fillna(0.0) * COST
    strat = (gross - cost).iloc[260:]
    bench = px["QQQ"].pct_change().reindex(strat.index)
    strat = strat[strat.index.dayofweek < 5]
    mid = strat.index[len(strat) // 2]
    print(f"{len(ASSETS)} assets, {strat.index[0].date()} -> {strat.index[-1].date()}, split {mid.date()}")
    for label, sl in (("H1", strat.index < mid), ("H2", strat.index >= mid), ("Full", slice(None))):
        s = alpha_stats(strat[sl], bench[sl])
        if s is None:
            print(label, "not measurable")
            continue
        print(f"{label}: alpha {s.alpha:+.2%}/yr t {s.t:.2f} beta {s.beta:.2f} corr {s.corr:.2f} n {s.n}")
    cum = (1 + strat).cumprod()
    print(f"CAGR {cum.iloc[-1] ** (252 / len(cum)) - 1:.2%}, max DD {(cum / cum.cummax() - 1).min():.1%}")


if __name__ == "__main__":
    run()
