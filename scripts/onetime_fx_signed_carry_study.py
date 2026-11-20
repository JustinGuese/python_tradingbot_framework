"""Signed (long/short) FX carry on the six CurrencyShares ETFs, bot-feasible (yfinance only)."""

import os
import sys
import warnings

import numpy as np
import pandas as pd
import yfinance as yf

os.environ.setdefault("POSTGRES_URI", "x:y@127.0.0.1:1/z")
warnings.filterwarnings("ignore")
from tradingbot.utils.alpha_report import alpha_stats  # noqa: E402

FX = {
    "FXE": ("EURUSD=X", False),
    "FXB": ("GBPUSD=X", False),
    "FXA": ("AUDUSD=X", False),
    "FXC": ("USDCAD=X", True),
    "FXF": ("USDCHF=X", True),
    "FXY": ("USDJPY=X", True),
}
CACHE = "/tmp/fxs_cache.pkl"
if os.path.exists(CACHE):
    px = pd.read_pickle(CACHE)
else:
    t = list(FX) + [v[0] for v in FX.values()] + ["QQQ", "^IRX"]
    raw = yf.download(t, start="2006-01-01", auto_adjust=True, progress=False)["Close"]
    raw.index = pd.DatetimeIndex(raw.index).tz_localize(None).normalize()
    px = raw.sort_index()
    px.to_pickle(CACHE)
print(px.dropna(how="all").index[[0, -1]], file=sys.stderr)

etf = px[list(FX)]
spot = pd.DataFrame({e: (1 / px[p] if inv else px[p]) for e, (p, inv) in FX.items()})
rf = (px["^IRX"].ffill() / 100 / 252).reindex(etf.index)
ret = etf.pct_change()
qqq = px["QQQ"].pct_change()


def carry_signal(win):
    return etf.pct_change(win) - spot.pct_change(win)


def run(k=2, win=252, gross=1.0, borrow=0.005, cash_credit=False, rebal="M", mom=None, cost=0.0005, signal=None):
    sig = signal if signal is not None else carry_signal(win)
    w = pd.DataFrame(0.0, index=etf.index, columns=etf.columns)
    last = None
    for i, d in enumerate(etf.index):
        key = (d.year, d.month) if rebal == "M" else (d.year, d.quarter if hasattr(d, "quarter") else 0)
        if key != last:
            last = key
            s = sig.loc[d].dropna()
            if len(s) >= 2 * k:
                rk = s.sort_values()
                long, short = list(rk.index[-k:]), list(rk.index[:k])
                row = pd.Series(0.0, index=etf.columns)
                row[long] = gross / 2 / k
                row[short] = -gross / 2 / k
                cur = row
            else:
                cur = pd.Series(0.0, index=etf.columns)
        w.iloc[i] = cur
    wl = w.shift(1).fillna(0.0)  # decide at close t, earn t+1
    gr = (wl * ret.fillna(0)).sum(axis=1)
    turn = wl.diff().abs().sum(axis=1).fillna(0)
    shortcost = (-wl.clip(upper=0)).sum(axis=1) * borrow / 252
    r = gr - turn * cost - shortcost
    if cash_credit:
        r = r + rf.fillna(0)  # collateral/idle cash earns T-bill (parity basis)
    return r


def report(name, r):
    r = r.dropna()
    q = qqq.reindex(r.index)
    ok = r.index >= pd.Timestamp("2007-01-01")  # warmup
    r, q = r[ok], q[ok]
    r = r[r.index.dayofweek < 5]
    q = q.reindex(r.index)
    st = alpha_stats(r, q)
    mid = len(r) // 2
    h1 = alpha_stats(r.iloc[:mid], q.iloc[:mid])
    h2 = alpha_stats(r.iloc[mid:], q.iloc[mid:])
    eq = (1 + r).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    cagr = eq.iloc[-1] ** (252 / len(r)) - 1

    def f(s):
        return f"{s.alpha * 100:+.2f}% t{s.t:+.2f}" if s else "n/a"

    print(
        f"{name:52s} CAGR {cagr * 100:+5.2f}% vol {r.std() * np.sqrt(252) * 100:4.1f}% DD {dd * 100:6.1f}% | full {f(st)} beta {st.beta:+.2f} | H1 {f(h1)} | H2 {f(h2)}"
    )
    return st


print("--- framework basis (cash earns 0%) ---")
for k in (1, 2, 3):
    for g in (1.0, 2.0):
        report(f"signed carry k={k} gross={g} win=252", run(k=k, gross=g))
print("--- parity basis (T-bill on collateral) ---")
for k in (2, 3):
    for g in (1.0, 2.0):
        report(f"signed carry k={k} gross={g} win=252 +rf", run(k=k, gross=g, cash_credit=True))
print("--- window / borrow sensitivity (k=2, gross 1, +rf) ---")
for win in (126, 189, 252, 378):
    report(f"win={win}", run(k=2, win=win, cash_credit=True))
for b in (0.0, 0.01, 0.02):
    report(f"borrow={b}", run(k=2, borrow=b, cash_credit=True))
print("--- long-only baseline (bot v2 analogue: top-3 > rf, gross 1/3 each) ---")
print("--- carry signal only vs total-return momentum (k=2) ---")
mom = etf.pct_change(252)
report("rank by 12m ETF total return (carry+spot)", run(k=2, signal=mom, cash_credit=True))
report("rank by carry only", run(k=2, cash_credit=True))
