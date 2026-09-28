"""Walk-forward test of PredictionMarketOverlayBot against its ablations.

Gate (docs/backtests/prediction-markets-2026-09.md): the prediction-market (PM)
overlay ships only if its H2 alpha_t vs QQQ is >= 2 AND it beats
- `signal="sma200"`: the same book, with "QQQ below its 200-day" in place of the
  PM risk score. A PM signal that only matches a moving average adds nothing.
- `signal="static"`: the same book with no overlay (r = 0 always).

Protocol: grid-search the PM parameters on H1 by alpha_t, then judge the H1
winner and the defaults on H2, which the search never saw. H1 starts where the
features start: the Fed path needs ladders ~6 months out, which exist from
2021-12-27, and recession odds from 2022-07. Split at 2024-01-01.

Prediction-market features are read once from the real database
(`prediction_market_snapshots`, filled by `predictionmarketsnapshot --backfill`)
and cached. Bot rows then go to a scratch SQLite file and prices come from
yfinance in memory, so nothing is written to Postgres.

Usage:
    kubectl port-forward -n tradingbots-2025 svc/psql-service 5432:5432
    POSTGRES_URI=... PYTHONPATH=. uv run python scripts/onetime_prediction_overlay_walkforward.py
"""

import argparse
import itertools
import json
import logging
import os
import pickle
import sys
import warnings

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd  # noqa: E402
import yfinance as yf  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import tradingbot.utils.db as db  # noqa: E402
from tradingbot.utils import prediction_market_features as pmf  # noqa: E402

CACHE = os.environ.get("RESEARCH_CACHE", "/tmp")
START, SPLIT = "2021-12-27", "2024-01-01"
PRICE_START = "2020-06-01"  # SMA200 warm-up before START


def load_features() -> pd.DataFrame:
    path = os.path.join(CACHE, "pm_snapshots.pkl")
    if os.path.exists(path):
        with open(path, "rb") as fh:
            return pickle.load(fh)
    db.init_db()
    snaps = pmf.load_snapshots(["recession", "fed_rate"])
    with open(path, "wb") as fh:
        pickle.dump(snaps, fh)
    return snaps


def offline(snaps: pd.DataFrame) -> None:
    """Scratch SQLite for bot rows, yfinance in memory, PM features from the cached frame."""
    eng = create_engine(f"sqlite:///{CACHE}/pm_overlay_walkforward.db", connect_args={"check_same_thread": False})
    db.Base.metadata.create_all(eng)
    db.SessionLocal = sessionmaker(bind=eng, autocommit=False, autoflush=False)
    db._schema_initialized = True
    daily = pmf.daily_features(snaps)
    pmf.feature_frame = lambda bar_dates, series=None: pmf.as_of_bars(daily, bar_dates)

    from tradingbot.utils.data_service import DataService

    cache: dict = {}

    def _yf(self, symbol, interval="1d", period="1y", save_to_db=False, use_cache=True):
        if symbol not in cache:
            raw = yf.download(symbol, start=PRICE_START, interval=interval, auto_adjust=True, progress=False)
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)
            df = raw.dropna().reset_index()
            df.columns = [str(c).lower() for c in df.columns]
            df = df.rename(columns={"date": "timestamp", "datetime": "timestamp"})
            df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
            df["symbol"] = symbol
            cache[symbol] = df[["symbol", "timestamp", "open", "high", "low", "close", "volume"]]
        return cache[symbol].copy()

    DataService.get_yf_data = _yf


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", default=START)
    parser.add_argument("--split", default=SPLIT)
    args = parser.parse_args()

    offline(load_features())
    from tradingbot.predictionmarketoverlaybot import PredictionMarketOverlayBot as Overlay
    from tradingbot.utils.backtest import _close_series, backtest_bot
    from tradingbot.utils.data_service import DataService

    base = Overlay()
    frames = {t: base.getYFDataWithTA(symbol=t, interval="1d", period="max") for t in base.tickers}
    bench = _close_series(DataService.get_yf_data(None, "QQQ"))
    halves = {"H1": (args.start, args.split), "H2": (args.split, "2100-01-01"), "FULL": (args.start, "2100-01-01")}

    def cut(lo, hi):
        return {t: f[(f.timestamp >= lo) & (f.timestamp < hi)].reset_index(drop=True) for t, f in frames.items()}

    data = {h: cut(lo, hi) for h, (lo, hi) in halves.items()}

    def score(params: dict, half: str) -> dict:
        r = backtest_bot(
            Overlay(**params), data=data[half], benchmark_close=bench, save_to_db=False, save_results_to_db=False
        )
        keys = ("alpha", "alpha_t", "beta", "benchmark_corr", "yearly_return", "maxdrawdown", "nrtrades")
        return {k: r.get(k) for k in keys}

    grid = Overlay.param_grid
    combos = [dict(zip(grid, v, strict=True)) for v in itertools.product(*grid.values())]
    best, best_t = None, float("-inf")
    for params in combos:
        s = score(params, "H1")
        if s["alpha_t"] is not None and s["nrtrades"] and s["alpha_t"] > best_t:
            best, best_t = params, s["alpha_t"]

    variants = {
        "pm_default": {},
        "pm_h1_best": best or {},
        "sma200": {"signal": "sma200"},
        "static": {"signal": "static"},
    }
    out = {
        "window": halves,
        "combos": len(combos),
        "best_params": best,
        "results": {name: {h: score(p, h) for h in halves} for name, p in variants.items()},
    }
    print(json.dumps(out, indent=1, default=float))
    rows = [
        {"variant": name, "half": h, **dict(s.items())} for name, per in out["results"].items() for h, s in per.items()
    ]
    print(pd.DataFrame(rows).round(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
