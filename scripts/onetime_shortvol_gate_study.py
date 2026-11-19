import os
import warnings

import numpy as np
import pandas as pd
import yfinance as yf

os.environ.setdefault("POSTGRES_URI", "x:y@127.0.0.1:1/z")
warnings.filterwarnings("ignore")
from tradingbot.utils.alpha_report import alpha_stats  # noqa: E402

cp = "/tmp/svol.pkl"
if os.path.exists(cp):
    px = pd.read_pickle(cp)
else:
    px = yf.download(
        ["SVXY", "VIXM", "^VIX", "^VIX3M", "^VIX9D", "SPY", "QQQ", "^IRX"],
        start="2011-01-01",
        auto_adjust=True,
        progress=False,
    )["Close"]
    px.index = pd.DatetimeIndex(px.index).tz_localize(None).normalize()
    px = px.sort_index()
    px.to_pickle(cp)
r = px.pct_change()
rf = px["^IRX"].ffill() / 100 / 252
vix, v3 = px["^VIX"], px["^VIX3M"]
ratio = v3 / vix
rv20 = r["SPY"].rolling(20).std() * np.sqrt(252) * 100
vrp = vix - rv20
volsvxy = r["SVXY"].rolling(20).std() * np.sqrt(252)


def sim(gate, hedge=0.0, tv=0.15, cap=0.5, start="2018-03-01", cost=0.0005, cash=True):
    g = gate.shift(1).fillna(False)
    size = (tv / volsvxy.shift(1)).clip(upper=cap).fillna(0)
    w = size * g
    rr = w * r["SVXY"] - hedge * w * 0 + (-0)  # hedge funded from sleeve below
    rr = w * (1 - hedge) * r["SVXY"] + w * hedge * r["VIXM"]
    rr = rr - w.diff().abs().fillna(0) * cost
    if cash:
        rr = rr + (1 - w) * rf  # parity: idle cash earns T-bill
    return rr[start:]


qq = r["QQQ"]


def rep(name, x, show=True):
    x = x.dropna()
    x = x[x.index.dayofweek < 5]
    q = qq.reindex(x.index)
    st = alpha_stats(x, q)
    m = len(x) // 2
    h1 = alpha_stats(x.iloc[:m], q.iloc[:m])
    h2 = alpha_stats(x.iloc[m:], q.iloc[m:])
    eq = (1 + x).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    cagr = eq.iloc[-1] ** (252 / len(x)) - 1

    def f(s):
        return f"{s.alpha * 100:+5.1f}% t{s.t:+.2f}"

    print(
        f"{name:46s} CAGR {cagr * 100:+5.1f}% DD {dd * 100:6.1f}% | {f(st)} beta {st.beta:+.2f} | H1 {f(h1)} | H2 {f(h2)}"
    )


print("n days since 2018-03:", len(r["2018-03-01":]))
rep("shipped-ish: ratio>1.0, hedge .15 VIXM", sim(ratio > 1.0, hedge=0.15))
rep("ratio>1.0 no hedge", sim(ratio > 1.0))
for c in (1.05, 1.10, 1.15):
    rep(f"ratio>{c} no hedge", sim(ratio > c))
for t in (0, 2, 4):
    rep(f"ratio>1.0 & VRP>{t}", sim((ratio > 1.0) & (vrp > t)))
rep("ratio>1.05 & VRP>3 & VIX<25", sim((ratio > 1.05) & (vrp > 3) & (vix < 25)))
rep("ratio>1.0 & VIX<20", sim((ratio > 1.0) & (vix < 20)))
rep("ratio>1.0 & VIX<25 hedge .15", sim((ratio > 1.0) & (vix < 25), hedge=0.15))
rep("ratio>1.0 & VIX9D<VIX (front contango)", sim((ratio > 1.0) & (px["^VIX9D"] < vix)))
# buy&hold references
rep("SVXY buy&hold", r["SVXY"]["2018-03-01":])
rep("QQQ buy&hold", r["QQQ"]["2018-03-01":])
print("--- vol-target sensitivity (ratio>1.05 & VRP>0) ---")
for tv in (0.08, 0.15, 0.25):
    rep(f"tv={tv}", sim((ratio > 1.05) & (vrp > 0), tv=tv))
