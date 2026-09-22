"""Backtest candidate C2 blends of low-beta sleeves; weights fit on H1 only.

Offline: bot rows go to a scratch SQLite file and data comes from yfinance.
Needs $RESEARCH_CACHE/fg.json, CNN's Fear & Greed history:
  curl -A "Mozilla/5.0" -H "Referer: https://edition.cnn.com/" \\
    https://production.dataviz.cnn.io/index/fearandgreed/graphdata/2021-01-01 -o fg.json
Results: docs/backtests/c2-blend-2026-09.md
"""

import json
import logging
import os
import warnings

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)
os.environ.setdefault("POSTGRES_URI", "stub:stub@localhost:5432/stub")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yfinance as yf  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import tradingbot.utils.db as db  # noqa: E402

# Cache dir for downloads (fg.json, per-symbol earnings). Kept out of the repo.
S = os.environ.get("RESEARCH_CACHE", "/tmp")
START, SPLIT, END = "2019-01-01", "2023-07-01", "2100-01-01"
eng = create_engine(f"sqlite:///{S}/blend.db", connect_args={"check_same_thread": False})
db.Base.metadata.create_all(eng)
db.SessionLocal = sessionmaker(bind=eng, autocommit=False, autoflush=False)
db._schema_initialized = True

from tradingbot.utils.data_service import DataService  # noqa: E402

_cache: dict = {}


def _yf(self, symbol, interval="1d", period="1y", save_to_db=False, use_cache=True):
    if (symbol, interval) not in _cache:
        import time

        for attempt in range(4):
            raw = yf.download(symbol, start="2017-01-01", interval=interval, auto_adjust=True, progress=False)
            if raw is not None and len(raw) > 10:
                break
            time.sleep(3 * (attempt + 1))
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        df = raw.dropna().reset_index()
        df.columns = [str(c).lower() for c in df.columns]
        df = df.rename(columns={"date": "timestamp"})
        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
        df["symbol"] = symbol
        _cache[(symbol, interval)] = df[["symbol", "timestamp", "open", "high", "low", "close", "volume"]]
    return _cache[(symbol, interval)].copy()


DataService.get_yf_data = _yf

from tradingbot.goldenbutterflymombot import GoldenButterflyMomBot  # noqa: E402
from tradingbot.taregimemultiassetbot import TARegimeMultiAssetBot  # noqa: E402
from tradingbot.tsmomtrendbot import TSMOMTrendBot  # noqa: E402
from tradingbot.utils.backtest import backtest_bot  # noqa: E402


def sleeve_returns(cls) -> pd.Series:
    bot = cls()
    frames = {t: bot.getYFDataWithTA(symbol=t, interval="1d", period="max") for t in bot.tickers}
    frames = {t: f[f.timestamp >= "2017-06-01"].reset_index(drop=True) for t, f in frames.items()}
    r = backtest_bot(bot, data=frames, save_to_db=False, save_results_to_db=False, return_series=True)
    eq = pd.Series({pd.Timestamp(p["t"]).tz_localize(None).normalize(): p["v"] for p in r["equity_curve"]})
    return eq.sort_index().pct_change().dropna()


def fg_inverse_returns() -> pd.Series:
    with open(f"{S}/fg.json") as fh:
        d = json.load(fh)["fear_and_greed_historical"]["data"]
    fg = pd.Series({pd.Timestamp(p["x"], unit="ms").normalize(): p["y"] for p in d}).sort_index()
    q = _yf(None, "QQQ").set_index("timestamp")["close"].pct_change()
    fg = fg.reindex(q.index).ffill()
    pos, out = 0, []
    for t in range(len(q) - 1):
        v = fg.iloc[t]
        if pd.notna(v):
            if v <= 30:
                pos = 1
            elif v >= 50:
                pos = 0
        out.append(pos)
    held = pd.Series(out, index=q.index[1:])
    # 5 bps on each position change, like the framework's slippage.
    cost = held.diff().abs().fillna(0) * 0.0005
    return (held * q.iloc[1:] - cost).dropna()


def stats(ret: pd.Series, q: pd.Series) -> dict:
    x = pd.concat({"r": ret, "q": q}, axis=1, join="inner").dropna()
    b = x.r.cov(x.q) / x.q.var()
    res = x.r - b * x.q
    eq = (1 + x.r).cumprod()
    return {
        "n": len(x),
        "cagr": eq.iloc[-1] ** (252 / len(x)) - 1,
        "vol": x.r.std() * np.sqrt(252),
        "beta": b,
        "corr": x.r.corr(x.q),
        "alpha": res.mean() * 252,
        "t": res.mean() / res.std() * np.sqrt(len(res)),
        "maxdd": (eq / eq.cummax() - 1).min(),
    }


def main():
    q = _yf(None, "QQQ").set_index("timestamp")["close"].pct_change().dropna()
    sleeves = {
        "TARegimeMultiAsset": sleeve_returns(TARegimeMultiAssetBot),
        "TSMOM": sleeve_returns(TSMOMTrendBot),
        "GoldenButterfly": sleeve_returns(GoldenButterflyMomBot),
        "FearGreedInverse": fg_inverse_returns(),
    }
    R = pd.DataFrame(sleeves).dropna()
    R = R[R.index >= START]
    print("common window", R.index[0].date(), "->", R.index[-1].date(), "n", len(R))
    print("\nsleeve correlations:\n", R.corr().round(2).to_string())
    h1 = R[R.index < SPLIT]
    inv = 1 / h1.std()
    w_iv = (inv / inv.sum()).round(3)
    blends = {
        "equal": pd.Series(1 / R.shape[1], index=R.columns),
        "inverse-vol (H1 fit)": w_iv,
        "3-sleeve inv-vol (no FG)": (inv.drop("FearGreedInverse") / inv.drop("FearGreedInverse").sum()).round(3),
    }
    rows = []
    for name, col in list(R.items()) + [(n, (R[w.index] * w).sum(axis=1)) for n, w in blends.items()]:
        for h, (lo, hi) in {"H1": (START, SPLIT), "H2": (SPLIT, END), "FULL": (START, END)}.items():
            s = stats(col[(col.index >= lo) & (col.index < hi)], q)
            rows.append({"series": name, "window": h, **s})
    t = pd.DataFrame(rows)
    for c in ("cagr", "vol", "alpha", "maxdd"):
        t[c] = (t[c] * 100).round(1)
    for c in ("beta", "corr", "t"):
        t[c] = t[c].round(2)
    print("\n", t.to_string(index=False))
    print("\nweights:", {k: v.to_dict() for k, v in blends.items()})
    stats_q = {
        h: stats(q[(q.index >= lo) & (q.index < hi)], q)
        for h, (lo, hi) in {"H1": (START, SPLIT), "H2": (SPLIT, END)}.items()
    }
    print("QQQ cagr:", {h: round(v["cagr"] * 100, 1) for h, v in stats_q.items()})


if __name__ == "__main__":
    main()
