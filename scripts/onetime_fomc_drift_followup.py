# ruff: noqa
# Follow-up to event-macro-2026-11.md. Run: PYTHONPATH=. python <this file> (pool needs a port-forward to the prod DB).
import os
import pickle

import numpy as np
import pandas as pd

os.environ.setdefault("POSTGRES_URI", "postgresql://x:x@localhost:5432/x")
from tradingbot.utils.macro_calendar import FOMC_STATEMENT_DATES

d = pickle.load(open("/tmp/event_macro_prices.pkl", "rb"))
print({k: (str(v.index[0].date()), len(v)) for k, v in d.items()})
px = pd.DataFrame(d).sort_index()
r = px.pct_change()
ev = [pd.Timestamp(x) for x in FOMC_STATEMENT_DATES if pd.Timestamp(x) <= px.index[-1]]
idx = px.index


def window(asset, a, b, days):
    """return per-event cumulative return entering close of T0+a, exiting close of T0+b"""
    out = []
    for e in days:
        i = idx.searchsorted(e)
        if i + b >= len(idx) or i + a < 0:
            continue
        s = r[asset].iloc[i + a + 1 : i + b + 1]
        if s.isna().any() or len(s) == 0:
            continue
        out.append((idx[i], (1 + s).prod() - 1))
    return pd.Series(dict(out))


def tstat(x):
    return x.mean() / x.std(ddof=1) * np.sqrt(len(x))


for asset, (a, b) in [("UUP", (0, 2)), ("UUP", (0, 1)), ("UUP", (0, 3)), ("UUP", (-1, 2))]:
    x = window(asset, a, b, ev)
    # abnormal vs unconditional mean over same-length windows
    q = window(asset, a, b, list(idx[::1]))  # all days
    ab = x - q.mean()
    h = len(x) // 2
    print(
        asset,
        (a, b),
        "n",
        len(x),
        "mean %.3f%% t %.2f | abnormal %.3f%% t %.2f | H1 %.3f%% (t %.2f) H2 %.3f%% (t %.2f) win %.0f%%"
        % (
            x.mean() * 100,
            tstat(x),
            ab.mean() * 100,
            tstat(ab),
            x[:h].mean() * 100,
            tstat(x[:h]),
            x[h:].mean() * 100,
            tstat(x[h:]),
            (x > 0).mean() * 100,
        ),
    )
# placebo: random 8/yr dates, same window, UUP[0,2]
x = window("UUP", 0, 2, ev)
obs = tstat(x)
rng = np.random.default_rng(0)
allw = window("UUP", 0, 2, list(idx))
allw = allw[allw.index >= x.index[0]]
cnt = 0
N = 5000
for _ in range(N):
    s = allw.sample(len(x), random_state=int(rng.integers(1e9)))
    if tstat(s) >= obs:
        cnt += 1
print("placebo p(t>=obs)=", cnt / N, "obs t", round(obs, 2))
# by-year
print((x.groupby(x.index.year).mean() * 100).round(2).to_dict())
# drop 2008-09
y = x[(x.index.year < 2008) | (x.index.year > 2009)]
print("ex-GFC mean %.3f%% t %.2f" % (y.mean() * 100, tstat(y)))
