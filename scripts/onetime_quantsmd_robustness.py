"""Robustness analysis for QuantsmdSmaDailyGatedBot.

Offline backtest runner measuring parameter sensitivity, cost drag,
sector concentration, and regime subperiod stability.

Output:
    Single JSON object with keys:
      gate_window:   alpha, t, beta, maxdd, trades across [100, 150, 200, 250, 300]
      costs:         slippage drag at 5 bps, 10 bps, 20 bps
      leave_one_out: single-sector deletion across the 9 sectors
      subperiods:    four non-overlapping regimes (2000-06, 2007-12, 2013-19, 2020-26)

Usage:
    uv run python scripts/onetime_quantsmd_robustness.py > /tmp/quantsmd_robustness.json
    uv run python scripts/onetime_quantsmd_robustness.py --section gate_window
"""

import argparse
import json
import logging
import os
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

import tradingbot.quantsmd_sma_daily_gated as mod  # noqa: E402
import tradingbot.utils.db as db  # noqa: E402
from tradingbot.utils.backtest import _close_series, backtest_bot  # noqa: E402
from tradingbot.utils.data_service import DataService  # noqa: E402

SCRATCH = os.environ.get("RETUNE_SCRATCH", "/tmp")
FETCH_START = "2000-01-01"

eng = create_engine(f"sqlite:///{SCRATCH}/quantsmd_robustness.db", connect_args={"check_same_thread": False})
db.Base.metadata.create_all(eng)
db.SessionLocal = sessionmaker(bind=eng, autocommit=False, autoflush=False)
db._schema_initialized = True

_cache: dict = {}


def _yf(self, symbol, interval="1d", period="1y", save_to_db=False, use_cache=True):
    key = (symbol, interval)
    if key not in _cache:
        raw = None
        for attempt in range(4):
            if interval == "1d":
                raw = yf.download(symbol, start=FETCH_START, interval=interval, auto_adjust=True, progress=False)
            else:
                raw = yf.download(symbol, period=period, interval=interval, auto_adjust=True, progress=False)
            if raw is not None and len(raw) > 10:
                break
            time.sleep(3 * (attempt + 1))
        if raw is None or raw.empty:
            raise ValueError(f"Failed to fetch data for {symbol}")
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


def _max_dd(values) -> float:
    v = np.asarray(values, dtype=float)
    return float((v / np.maximum.accumulate(v) - 1).min()) if len(v) else 0.0


def load(bot) -> dict[str, pd.DataFrame]:
    tickers = bot.tickers or [bot.symbol]
    return {t: bot.getYFDataWithTA(symbol=t, interval=bot.interval, period="max") for t in tickers}


def cut(frames: dict[str, pd.DataFrame], lo: str, hi: str) -> dict[str, pd.DataFrame]:
    return {t: f[(f.timestamp >= lo) & (f.timestamp < hi)].reset_index(drop=True) for t, f in frames.items()}


def score_run(
    bot,
    data: dict[str, pd.DataFrame],
    bench: pd.Series,
    slippage_pct: float = 0.0005,
) -> dict:
    r = backtest_bot(
        bot,
        data=data,
        benchmark_close=bench,
        slippage_pct=slippage_pct,
        save_to_db=False,
        save_results_to_db=False,
        return_series=True,
    )
    values = [p["v"] for p in r.get("equity_curve", [])]
    return {
        "alpha": r.get("alpha"),
        "alpha_t": r.get("alpha_t"),
        "beta": r.get("beta"),
        "maxdd": _max_dd(values) if values else r.get("maxdrawdown"),
        "trades": r.get("nrtrades"),
    }


def run_gate_window(frames: dict[str, pd.DataFrame], bench: pd.Series) -> dict:
    results = {}
    orig_w = mod.REGIME_WINDOW
    try:
        for w in (100, 150, 200, 250, 300):
            mod.REGIME_WINDOW = w
            bot = mod.QuantsmdSmaDailyGatedBot()
            results[str(w)] = score_run(bot, data=frames, bench=bench)
    finally:
        mod.REGIME_WINDOW = orig_w
    return results


def run_costs(frames: dict[str, pd.DataFrame], bench: pd.Series) -> dict:
    results = {}
    bot = mod.QuantsmdSmaDailyGatedBot()
    costs = {
        "5bps": 0.0005,
        "10bps": 0.0010,
        "20bps": 0.0020,
    }
    for label, slip in costs.items():
        results[label] = score_run(bot, data=frames, bench=bench, slippage_pct=slip)
    return results


def run_leave_one_out(frames: dict[str, pd.DataFrame], bench: pd.Series) -> dict:
    results = {}
    orig_universe = list(mod.UNIVERSE)
    try:
        for dropped in orig_universe:
            mod.UNIVERSE = [s for s in orig_universe if s != dropped]
            bot = mod.QuantsmdSmaDailyGatedBot()
            sub_frames = {t: frames[t] for t in bot.tickers}
            results[dropped] = score_run(bot, data=sub_frames, bench=bench)
    finally:
        mod.UNIVERSE = orig_universe
    return results


def run_subperiods(frames: dict[str, pd.DataFrame], bench: pd.Series) -> dict:
    results = {}
    periods = {
        "2000-2006": ("2000-01-01", "2007-01-01"),
        "2007-2012": ("2007-01-01", "2013-01-01"),
        "2013-2019": ("2013-01-01", "2020-01-01"),
        "2020-2026": ("2020-01-01", "2027-01-01"),
    }
    for label, (lo, hi) in periods.items():
        sub_frames = cut(frames, lo, hi)
        bot = mod.QuantsmdSmaDailyGatedBot()
        results[label] = score_run(bot, data=sub_frames, bench=bench)
    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Robustness analysis for QuantsmdSmaDailyGatedBot across gate windows, costs, sectors, and subperiods."
    )
    parser.add_argument(
        "--section",
        choices=["gate_window", "costs", "leave_one_out", "subperiods"],
        help="Run one section only so sections can run in parallel (default: all).",
    )
    return parser.parse_args(argv)


def main():
    args = parse_args()
    base_bot = mod.QuantsmdSmaDailyGatedBot()
    frames = load(base_bot)
    qqq_frame = _yf(None, "QQQ")
    bench = _close_series(qqq_frame)

    runners = {
        "gate_window": lambda: run_gate_window(frames, bench),
        "costs": lambda: run_costs(frames, bench),
        "leave_one_out": lambda: run_leave_one_out(frames, bench),
        "subperiods": lambda: run_subperiods(frames, bench),
    }

    if args.section:
        out = {args.section: runners[args.section]()}
    else:
        out = {sec: runner() for sec, runner in runners.items()}

    print(json.dumps(out, default=float, indent=2))


if __name__ == "__main__":
    main()
