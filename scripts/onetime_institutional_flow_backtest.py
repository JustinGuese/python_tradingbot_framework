"""Walk-forward evaluation of InstitutionalFlowBot: grid on H1, judge on unseen H2.

Offline, like onetime_retune_walkforward.py: bot rows go to a scratch SQLite
file, market data comes from yfinance in memory, nothing touches Postgres.

Two benchmarks per run, because today's S&P 100 list is survivorship-biased:
  * vs QQQ              — the repo's standing target (CLAUDE.md)
  * vs EW universe hold — equal-weight buy-and-hold of the same stocks from the
    same start. The bias is in both sides, so this isolates the selection skill.

Universe: S&P 100 names with daily history from before FETCH_START. The
framework backtest inner-joins timestamps, so one recent listing would
otherwise truncate the whole window; the excluded names are printed.

Usage: uv run python scripts/onetime_institutional_flow_backtest.py
Results: docs/backtests/institutionalflowbot.md
"""

import itertools
import json
import logging
import os
import sys
import time
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

SCRATCH = os.environ.get("RETUNE_SCRATCH", "/tmp")
FETCH_START, START, SPLIT = "2017-01-01", "2019-01-01", "2023-01-01"
WINDOWS = {"H1": (START, SPLIT), "H2": (SPLIT, "2100-01-01"), "FULL": (START, "2100-01-01")}

eng = create_engine(f"sqlite:///{SCRATCH}/instflow.db", connect_args={"check_same_thread": False})
db.Base.metadata.create_all(eng)
db.SessionLocal = sessionmaker(bind=eng, autocommit=False, autoflush=False)
db._schema_initialized = True

from tradingbot.utils.data_service import DataService  # noqa: E402

_cache: dict = {}


def _yf(self, symbol, interval="1d", period="1y", save_to_db=False, use_cache=True):
    key = (symbol, interval)
    if key not in _cache:
        raw = yf.download(symbol, start=FETCH_START, interval=interval, auto_adjust=True, progress=False)
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

from tradingbot.institutionalflowbot import BENCHMARK, InstitutionalFlowBot  # noqa: E402
from tradingbot.utils.backtest import _close_series, _compute_alpha_metrics, backtest_bot  # noqa: E402


def load_frames() -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Feature frames for every ticker with full history; the rest are reported and dropped."""
    probe = InstitutionalFlowBot()
    frames, excluded = {}, []
    cutoff = pd.Timestamp(FETCH_START) + pd.Timedelta(days=10)
    for t in probe.tickers:
        try:
            f = probe.getYFDataWithTA(symbol=t, interval="1d", period="max")
        except Exception as exc:
            excluded.append(f"{t} (fetch: {exc})")
            continue
        if f.empty or f["timestamp"].min() > cutoff:
            excluded.append(t)
            continue
        frames[t] = f
    return frames, excluded


def cut(frames, lo, hi):
    # Features were computed on the full history, so the window starts warm.
    return {t: f[(f.timestamp >= lo) & (f.timestamp < hi)].reset_index(drop=True) for t, f in frames.items()}


def _curve(points) -> tuple[list, list]:
    return [p["v"] for p in points], [pd.Timestamp(p["t"]) for p in points]


def _max_dd(values) -> float:
    v = np.asarray(values, dtype=float)
    return float((v / np.maximum.accumulate(v) - 1).min()) if len(v) else 0.0


def run(params: dict, data: dict, qqq: pd.Series, universe: list[str]) -> dict:
    bot = InstitutionalFlowBot(**params)
    bot.tickers = [*universe, BENCHMARK]
    r = backtest_bot(
        bot, data=data, benchmark_close=qqq, save_to_db=False, save_results_to_db=False, return_series=True
    )
    values, stamps = _curve(r["equity_curve"])
    ew_values, _ = _curve(r["buy_hold_curve"])
    ew = pd.Series(ew_values, index=stamps)
    vs_ew = _compute_alpha_metrics(values, stamps, ew, "1d")
    ew_vs_qqq = _compute_alpha_metrics(ew_values, stamps, qqq, "1d")
    return {
        "alpha": r["alpha"],
        "alpha_t": r["alpha_t"],
        "beta": r["beta"],
        "corr": r["benchmark_corr"],
        "maxdd": _max_dd(values),
        "cagr": r["yearly_return"],
        "trades": r["nrtrades"],
        "alpha_vs_ew": vs_ew["alpha"],
        "t_vs_ew": vs_ew["alpha_t"],
        "ew_alpha": ew_vs_qqq["alpha"],
        "ew_t": ew_vs_qqq["alpha_t"],
        "ew_beta": ew_vs_qqq["beta"],
        "ew_maxdd": _max_dd(ew_values),
    }


def main():
    t0 = time.time()
    frames, excluded = load_frames()
    universe = [t for t in frames if t != BENCHMARK]
    qqq = _close_series(frames[BENCHMARK])
    data = {w: cut(frames, lo, hi) for w, (lo, hi) in WINDOWS.items()}
    print(f"universe {len(universe)} names; excluded {excluded}; loaded in {time.time() - t0:.0f}s", file=sys.stderr)

    grid = InstitutionalFlowBot.param_grid
    combos = [dict(zip(grid, v, strict=True)) for v in itertools.product(*grid.values())]
    h1 = {}
    for p in combos:
        h1[json.dumps(p)] = run(p, data["H1"], qqq, universe)
        print(f"H1 {p}: alpha_t {h1[json.dumps(p)]['alpha_t']:.2f}", file=sys.stderr)
    best = max(combos, key=lambda p: h1[json.dumps(p)]["alpha_t"] or float("-inf"))

    configs = {"default (weekly)": {}, "default, daily": {"rebalance_weekday": None}, "H1 best": best}
    out = {
        "universe_size": len(universe),
        "excluded": excluded,
        "best_params": best,
        "grid_h1": h1,
        "results": {name: {w: run(p, data[w], qqq, universe) for w in WINDOWS} for name, p in configs.items()},
    }
    print(json.dumps(out, default=float, indent=1))
    print(f"done in {time.time() - t0:.0f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
