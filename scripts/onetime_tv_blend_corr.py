"""Correlation + incremental alpha of TvTrendDailyBot (ST AND AT) vs the C2 sleeves.

Reuses the C2 blend backtest harness (offline sqlite + yfinance). Results: docs/backtests/tv-trend-boost-2026-11.md
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np
import pandas as pd

import scripts.onetime_c2_blend_backtest as c2
from tradingbot.goldenbutterflymombot import GoldenButterflyMomBot
from tradingbot.taregimemultiassetbot import TARegimeMultiAssetBot
from tradingbot.tsmomtrendbot import TSMOMTrendBot
from tradingbot.tvtrenddailybot import TvTrendDailyBot


def main() -> None:
    q = c2.yf.download("QQQ", start="2017-01-01", auto_adjust=True, progress=False)["Close"].squeeze()
    q.index = pd.to_datetime(q.index).tz_localize(None)
    qr = q.pct_change().dropna()
    s = {
        "TARegime": c2.sleeve_returns(TARegimeMultiAssetBot),
        "GoldenButterfly": c2.sleeve_returns(GoldenButterflyMomBot),
        "TSMOM": c2.sleeve_returns(TSMOMTrendBot),
        "TvTrend": c2.sleeve_returns(TvTrendDailyBot),
    }
    df = pd.DataFrame(s).dropna()
    print("window", df.index[0].date(), df.index[-1].date(), len(df))
    print(df.corr().round(2))
    vol = df.iloc[: len(df) // 2].std()
    w3 = (1 / vol[["TARegime", "GoldenButterfly", "TSMOM"]]) / (1 / vol[["TARegime", "GoldenButterfly", "TSMOM"]]).sum()
    blend3 = (df[w3.index] * w3).sum(axis=1)
    w4 = (1 / vol) / (1 / vol).sum()
    blend4 = (df * w4).sum(axis=1)
    print("corr TvTrend vs blend3", round(df["TvTrend"].corr(blend3), 2))
    print("w4", w4.round(2).to_dict())
    h = len(df) // 2
    out = {"TvTrend": df["TvTrend"], "blend3": blend3, "blend3+TvTrend": blend4}
    for name, r in out.items():
        for lab, sl in [("H1", slice(0, h)), ("H2", slice(h, None)), ("full", slice(0, None))]:
            rr = r.iloc[sl]
            x = qr.reindex(rr.index).fillna(0)
            beta = np.cov(rr, x)[0, 1] / x.var()
            res = rr - beta * x
            a = res.mean() * 252
            t = res.mean() / res.std() * np.sqrt(len(rr))
            print(f"{name:16s} {lab:4s} alpha {a:+.2%} t {t:+.2f} beta {beta:.2f}")


if __name__ == "__main__":
    main()
