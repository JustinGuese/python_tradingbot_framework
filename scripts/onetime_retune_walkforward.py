"""Walk-forward re-tune on alpha_t: grid on H1, judge best vs defaults on unseen H2.

Offline: bot rows go to a scratch SQLite file, market data comes from yfinance in
memory, nothing is written to any Postgres.
Usage: uv run --with yfinance python scripts/onetime_retune_walkforward.py <module> <ClassName> [max_combos]
Results: docs/backtests/retune-2026-09.md
"""

import importlib
import itertools
import json
import logging
import os
import random
import sys
import warnings

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)
os.environ.setdefault("POSTGRES_URI", "stub:stub@localhost:5432/stub")

import pandas as pd  # noqa: E402
import yfinance as yf  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import tradingbot.utils.db as db  # noqa: E402

SCRATCH = os.environ.get("RETUNE_SCRATCH", "/tmp")
START, SPLIT = "2019-01-01", "2023-01-01"

eng = create_engine(f"sqlite:///{SCRATCH}/retune_{sys.argv[2]}.db", connect_args={"check_same_thread": False})
db.Base.metadata.create_all(eng)
db.SessionLocal = sessionmaker(bind=eng, autocommit=False, autoflush=False)
db._schema_initialized = True

from tradingbot.utils.data_service import DataService  # noqa: E402

_cache: dict = {}


def _yf(self, symbol, interval="1d", period="1y", save_to_db=False, use_cache=True):
    key = (symbol, interval)
    if key not in _cache:
        raw = yf.download(symbol, start=START, interval=interval, auto_adjust=True, progress=False)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        df = raw.dropna().reset_index()
        df.columns = [str(c).lower() for c in df.columns]
        df = df.rename(columns={"date": "timestamp", "datetime": "timestamp"})
        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
        df["symbol"] = symbol
        _cache[key] = df[["symbol", "timestamp", "open", "high", "low", "close", "volume"]]
    return _cache[key].copy()


DataService.get_yf_data = _yf

from tradingbot.utils.backtest import _close_series, backtest_bot  # noqa: E402


def load(bot):
    tickers = bot.tickers or [bot.symbol]
    frames = {t: bot.getYFDataWithTA(symbol=t, interval=bot.interval, period="max") for t in tickers}
    return frames


def cut(frames, lo, hi, single):
    out = {t: f[(f.timestamp >= lo) & (f.timestamp < hi)].reset_index(drop=True) for t, f in frames.items()}
    return next(iter(out.values())) if single else out


def score(cls, params, data, bench):
    bot = cls(**params)
    r = backtest_bot(bot, data=data, benchmark_close=bench, save_to_db=False, save_results_to_db=False)
    return {
        k: r.get(k) for k in ("alpha", "alpha_t", "beta", "benchmark_corr", "yearly_return", "maxdrawdown", "nrtrades")
    }


def main():
    mod, name = sys.argv[1], sys.argv[2]
    max_combos = int(sys.argv[3]) if len(sys.argv) > 3 else 60
    cls = getattr(importlib.import_module(mod), name)
    base = cls()
    from tradingbot.utils.botclass import Bot

    single = not (len(base.tickers or []) > 1 or type(base).targetWeights is not Bot.targetWeights)
    frames = load(base)
    bench = _close_series(_yf(None, "QQQ", base.interval))
    halves = {"H1": (START, SPLIT), "H2": (SPLIT, "2100-01-01"), "FULL": (START, "2100-01-01")}
    data = {h: cut(frames, lo, hi, single) for h, (lo, hi) in halves.items()}

    grid = getattr(cls, "param_grid", {}) or {}
    combos = [dict(zip(grid, v, strict=True)) for v in itertools.product(*grid.values())]
    random.Random(0).shuffle(combos)
    combos = combos[:max_combos]

    res = {"bot": name, "combos_tested": len(combos), "default": {h: score(cls, {}, data[h], bench) for h in halves}}
    best, best_t = None, float("-inf")
    for p in combos:
        try:
            s = score(cls, p, data["H1"], bench)
        except Exception:
            continue
        if s["alpha_t"] is not None and s["nrtrades"] and s["alpha_t"] > best_t:
            best, best_t = p, s["alpha_t"]
    if best is not None:
        res["best_params"] = best
        res["best"] = {h: score(cls, best, data[h], bench) for h in halves}
    print(json.dumps(res, default=float))


if __name__ == "__main__":
    main()
