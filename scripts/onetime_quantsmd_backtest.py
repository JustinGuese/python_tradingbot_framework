"""Backtest the ten quants.md (QuantJourney) strategy bots against QQQ.

Offline, like onetime_institutional_flow_backtest.py: bot rows go to a scratch SQLite
file, market data comes from yfinance in memory, nothing touches Postgres.

Each bot runs once over its whole common history. The H1/H2 columns are alpha
stats on the two halves of that one equity curve (not separate runs): a bot's
lookbacks need warmup, so cutting the data in half would starve the second half.

Usage: uv run python scripts/onetime_quantsmd_backtest.py > /tmp/quantsmd.json
Results: docs/backtests/quantsmd-2026-11.md
"""

import importlib
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
FETCH_START = "2000-01-01"

eng = create_engine(f"sqlite:///{SCRATCH}/quantsmd.db", connect_args={"check_same_thread": False})
db.Base.metadata.create_all(eng)
db.SessionLocal = sessionmaker(bind=eng, autocommit=False, autoflush=False)
db._schema_initialized = True

from tradingbot.utils.data_service import DataService  # noqa: E402

_cache: dict = {}


def _yf(self, symbol, interval="1d", period="1y", save_to_db=False, use_cache=True):
    key = (symbol, interval)
    if key not in _cache:
        raw = None
        for attempt in range(4):
            raw = yf.download(symbol, start=FETCH_START, interval=interval, auto_adjust=True, progress=False)
            if raw is not None and len(raw) > 10:
                break
            time.sleep(3 * (attempt + 1))
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

from tradingbot.utils.backtest import _close_series, _compute_alpha_metrics, backtest_bot  # noqa: E402

BOTS = [
    ("quantsmd_continuous_futures_trend", "QuantsmdContinuousFuturesTrendBot", 0.62),
    ("quantsmd_vol_target_momentum", "QuantsmdVolTargetMomentumBot", 0.57),
    ("quantsmd_monthly_rotation", "QuantsmdMonthlyRotationBot", 0.53),
    ("quantsmd_sma_daily", "QuantsmdSmaDailyBot", 0.45),
    ("quantsmd_monthly_inverse_vol", "QuantsmdMonthlyInverseVolBot", 0.45),
    ("quantsmd_risk_parity_capped", "QuantsmdRiskParityCappedBot", 0.45),
    ("quantsmd_quarterly_te_cost_gate", "QuantsmdQuarterlyTeCostGateBot", 0.44),
    ("quantsmd_bollinger_reversion", "QuantsmdBollingerReversionBot", 0.42),
    ("quantsmd_vol_target_trend", "QuantsmdVolTargetTrendBot", 0.36),
    ("quantsmd_weekly_rsi_reversion", "QuantsmdWeeklyRsiReversionBot", 0.34),
]


def _max_dd(values) -> float:
    v = np.asarray(values, dtype=float)
    return float((v / np.maximum.accumulate(v) - 1).min()) if len(v) else 0.0


def _stats(values, stamps, qqq) -> dict:
    m = _compute_alpha_metrics(values, stamps, qqq, "1d")
    return {"alpha": m["alpha"], "alpha_t": m["alpha_t"], "beta": m["beta"], "corr": m["benchmark_corr"]}


def run_one(module: str, cls_name: str, source_sharpe: float, qqq_frame: pd.DataFrame) -> dict:
    cls = getattr(importlib.import_module(f"tradingbot.{module}"), cls_name)
    bot = cls()
    frames = {t: bot.getYFDataWithTA(symbol=t, interval="1d", period="max") for t in bot.tickers}
    qqq = _close_series(qqq_frame)
    r = backtest_bot(bot, data=frames, benchmark_close=qqq, save_to_db=False, save_results_to_db=False, return_series=True)
    values = [p["v"] for p in r["equity_curve"]]
    stamps = [pd.Timestamp(p["t"]) for p in r["equity_curve"]]
    bh = [p["v"] for p in r["buy_hold_curve"]]
    mid = len(values) // 2
    q = qqq.copy()
    q.index = pd.to_datetime(q.index)
    years = max((stamps[-1] - stamps[0]).days / 365.25, 1e-9)
    qqq_window = q.reindex(stamps, method="ffill")
    return {
        "bot": cls_name,
        "source_sharpe": source_sharpe,
        "start": str(stamps[0].date()),
        "end": str(stamps[-1].date()),
        "bars": len(values),
        "alpha": r["alpha"],
        "alpha_t": r["alpha_t"],
        "beta": r["beta"],
        "corr": r["benchmark_corr"],
        "sharpe": r["sharpe_ratio"],
        "cagr": r["yearly_return"],
        "maxdd": _max_dd(values),
        "trades": r["nrtrades"],
        "qqq_cagr": float((qqq_window.iloc[-1] / qqq_window.iloc[0]) ** (1 / years) - 1),
        "bh_cagr": float((bh[-1] / bh[0]) ** (1 / years) - 1) if bh else None,
        "h1": _stats(values[:mid], stamps[:mid], q),
        "h2": _stats(values[mid:], stamps[mid:], q),
    }


def main():
    t0 = time.time()
    qqq_frame = _yf(None, "QQQ")
    only = set(sys.argv[1:])
    out = []
    for module, cls_name, sharpe in BOTS:
        if only and module not in only:
            continue
        try:
            out.append(run_one(module, cls_name, sharpe, qqq_frame))
            print(f"{cls_name}: alpha_t {out[-1]['alpha_t']}", file=sys.stderr)
        except Exception as exc:
            out.append({"bot": cls_name, "error": repr(exc)})
            print(f"{cls_name}: FAILED {exc!r}", file=sys.stderr)
    print(json.dumps(out, default=float, indent=1))
    print(f"done in {time.time() - t0:.0f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
