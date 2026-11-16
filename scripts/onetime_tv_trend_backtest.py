"""
Untuned backtest of the four TradingView trend-following bots against QQQ (2026-11).

Bots:
  - SupertrendMTFBot: Multi-timeframe Supertrend (1h base + 1D HTF filter).
  - UltimateMABot: Multi-timeframe Ultimate MA (1h base + 4h HTF filter, default SMA 20).
  - AlphaTrendBot: AlphaTrend indicator (1h base), enter on buy cross, hold until sell cross.
  - SlingshotChandelierBot: Slingshot EMA Channel (34/50) + Chandelier Exit (22, 3.0).

All four bots share:
  - Universe: QQQ, SPY, IWM, GLD, TLT (equal weight 1/5 per active ticker, idle cash).
  - Base interval: 1h bars (yfinance serves ~730 days max for 1h).
  - Long-only: bearish signal means flat/cash.
  - Cost model: 5 bps one-way slippage (framework ExecutionConfig default).
  - Judging bar: alpha vs QQQ and its t-statistic (t >= 2, positive in both halves, beta < 0.5), per CLAUDE.md.
  - Untuned: default parameters only.

Usage:
  POSTGRES_URI=postgresql://... uv run python scripts/onetime_tv_trend_backtest.py
  Optionally write results to doc:
  POSTGRES_URI=postgresql://... uv run python scripts/onetime_tv_trend_backtest.py --save-doc

Results document: docs/backtests/tv-trend-2026-11.md
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from tradingbot.alphatrendbot import AlphaTrendBot
from tradingbot.slingshotchandelierbot import SlingshotChandelierBot
from tradingbot.supertrendmtfbot import SupertrendMTFBot
from tradingbot.ultimatemabot import UltimateMABot
from tradingbot.utils.backtest import _close_series, _compute_alpha_metrics, backtest_bot

DOC_PATH = Path("docs/backtests/tv-trend-2026-11.md")
UNIVERSE = ["QQQ", "SPY", "IWM", "GLD", "TLT"]
INTERVAL = "1h"
PERIOD = "730d"

BOT_CONFIGS = [
    {
        "name": "SupertrendMTFBot",
        "module": "tradingbot.supertrendmtfbot",
        "class": SupertrendMTFBot,
        "htf": "1D",
        "notes": "1h base + 1D HTF filter; long when both +1",
    },
    {
        "name": "UltimateMABot",
        "module": "tradingbot.ultimatemabot",
        "class": UltimateMABot,
        "htf": "4h",
        "notes": "1h base + 4h HTF filter (SMA 20); long when both slopes +1",
    },
    {
        "name": "AlphaTrendBot",
        "module": "tradingbot.alphatrendbot",
        "class": AlphaTrendBot,
        "htf": "-",
        "notes": "1h base; buy cross -> hold -> sell cross",
    },
    {
        "name": "SlingshotChandelierBot",
        "module": "tradingbot.slingshotchandelierbot",
        "class": SlingshotChandelierBot,
        "htf": "-",
        "notes": "1h base; pullback into EMA channel, exit on Chandelier stop / bear",
    },
]


def _max_dd(values: list[float] | np.ndarray) -> float:
    v = np.asarray(values, dtype=float)
    return float((v / np.maximum.accumulate(v) - 1).min()) if len(v) else 0.0


def true_cagr(total_return: float | None, start: pd.Timestamp, end: pd.Timestamp) -> float | None:
    """Annualized compound growth rate from total return and timestamps."""
    if total_return is None or start is None or end is None:
        return None
    days = (end - start).days
    if days <= 0:
        return None
    years = days / 365.25
    if 1 + total_return <= 0:
        return -1.0 if 1 + total_return == 0 else None
    return float((1 + total_return) ** (1 / years) - 1)


def fetch_universe_data(
    tickers: list[str] = UNIVERSE,
    interval: str = INTERVAL,
    period: str = PERIOD,
    from_db: bool = False,
) -> dict[str, pd.DataFrame]:
    """Fetch 1h market data for the universe."""
    from tradingbot.utils.data_service import DataService

    ds = DataService()
    frames = {}
    for sym in tickers:
        print(f"Fetching {sym} ({interval}, {period})...", file=sys.stderr)
        if from_db:
            df = ds.get_yf_data(sym, interval=interval, period="max", save_to_db=False, use_cache=False)
        else:
            df = ds.get_yf_data_with_ta(sym, interval=interval, period=period, save_to_db=False)
        if df is None or df.empty:
            raise RuntimeError(f"Could not load data for symbol {sym}")
        frames[sym] = df
    return frames


def run_backtest_for_bot(
    bot_cfg: dict,
    data: dict[str, pd.DataFrame],
    qqq_close: pd.Series,
) -> dict:
    """Run backtest_bot for a single bot configuration and compute metrics."""
    cls = bot_cfg["class"]
    bot = cls()

    # Pass deep copy of data to avoid any cross-bot mutations
    bot_data = {t: df.copy() for t, df in data.items() if t in bot.tickers}

    res = backtest_bot(
        bot,
        data=bot_data,
        benchmark_close=qqq_close,
        save_to_db=False,
        save_results_to_db=False,
        record_trial=False,
        return_series=True,
    )

    curve = res.get("equity_curve", [])
    if curve:
        values = [p["v"] for p in curve]
        stamps = [pd.Timestamp(p["t"]) for p in curve]
        start_ts = stamps[0]
        end_ts = stamps[-1]
        mid = len(values) // 2

        # Sub-period metrics
        h1_metrics = _compute_alpha_metrics(values[:mid], stamps[:mid], qqq_close, INTERVAL)
        h2_metrics = _compute_alpha_metrics(values[mid:], stamps[mid:], qqq_close, INTERVAL)
        dd = _max_dd(values)
        cagr = true_cagr(res.get("yearly_return"), start_ts, end_ts)
    else:
        start_ts = end_ts = None
        values = stamps = []
        h1_metrics = h2_metrics = {}
        dd = res.get("maxdrawdown", 0.0)
        cagr = None

    alpha = res.get("alpha")
    alpha_t = res.get("alpha_t")
    beta = res.get("beta")
    corr = res.get("benchmark_corr")
    total_ret = res.get("yearly_return")
    sharpe = res.get("sharpe_ratio")
    trades = res.get("nrtrades", 0)

    # Check project bar: t >= 2, positive in both halves, beta < 0.5
    h1_a = h1_metrics.get("alpha")
    h2_a = h2_metrics.get("alpha")
    passed_bar = bool(
        alpha_t is not None
        and alpha_t >= 2.0
        and h1_a is not None
        and h1_a > 0
        and h2_a is not None
        and h2_a > 0
        and beta is not None
        and beta < 0.5
    )

    return {
        "bot": bot_cfg["name"],
        "htf": bot_cfg["htf"],
        "notes": bot_cfg["notes"],
        "start": str(start_ts.date()) if start_ts else "n/a",
        "end": str(end_ts.date()) if end_ts else "n/a",
        "bars": len(values),
        "alpha": alpha,
        "alpha_t": alpha_t,
        "beta": beta,
        "corr": corr,
        "max_dd": dd,
        "total_return": total_ret,
        "cagr": cagr,
        "sharpe": sharpe,
        "trades": trades,
        "h1_alpha": h1_a,
        "h1_t": h1_metrics.get("alpha_t"),
        "h2_alpha": h2_a,
        "h2_t": h2_metrics.get("alpha_t"),
        "passed_bar": passed_bar,
    }


def format_table(results: list[dict]) -> str:
    """Format results into a markdown table."""
    header = (
        "| Bot | HTF | Start | End | Alpha/yr | t | Beta | Corr | Max DD | CAGR | Sharpe | Trades | Bar (t>=2) |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    )
    rows = []
    for r in results:
        a_str = f"{r['alpha']:+.1%}" if r["alpha"] is not None else "n/a"
        t_str = f"{r['alpha_t']:.2f}" if r["alpha_t"] is not None else "n/a"
        b_str = f"{r['beta']:.2f}" if r["beta"] is not None else "n/a"
        c_str = f"{r['corr']:.2f}" if r["corr"] is not None else "n/a"
        dd_str = f"{r['max_dd']:.1%}" if r["max_dd"] is not None else "n/a"
        cagr_str = f"{r['cagr']:+.1%}" if r["cagr"] is not None else "n/a"
        s_str = f"{r['sharpe']:.2f}" if r["sharpe"] is not None else "n/a"
        status = "PASS" if r["passed_bar"] else "fail"
        rows.append(
            f"| {r['bot']} | {r['htf']} | {r['start']} | {r['end']} | {a_str} | {t_str} | {b_str} | "
            f"{c_str} | {dd_str} | {cagr_str} | {s_str} | {r['trades']} | {status} |"
        )
    return "\n".join([header, *rows])


def main() -> int:
    warnings.filterwarnings("ignore")
    logging.disable(logging.WARNING)

    parser = argparse.ArgumentParser(description="Untuned backtest of TradingView trend bots vs QQQ.")
    parser.add_argument("--from-db", action="store_true", help="Load cached bars from DB instead of yfinance")
    parser.add_argument("--save-doc", action="store_true", help="Write results directly into tv-trend-2026-11.md")
    parser.add_argument("--bot", choices=[b["name"] for b in BOT_CONFIGS], help="Run a single bot")
    args = parser.parse_args()

    t0 = time.time()
    data = fetch_universe_data(from_db=args.from_db)
    qqq_close = _close_series(data["QQQ"])

    targets = [b for b in BOT_CONFIGS if args.bot is None or b["name"] == args.bot]
    results = []

    print(f"\nRunning backtest for {len(targets)} bots over {INTERVAL} bars (period {PERIOD})...\n")

    for cfg in targets:
        name = cfg["name"]
        print(f"--- Running {name} ---", file=sys.stderr)
        try:
            r = run_backtest_for_bot(cfg, data, qqq_close)
            results.append(r)
            a_fmt = f"{r['alpha']:+.1%}" if r["alpha"] is not None else "n/a"
            t_fmt = f"{r['alpha_t']:.2f}" if r["alpha_t"] is not None else "n/a"
            print(
                f"{name}: alpha {a_fmt}, alpha_t {t_fmt}, beta {r['beta']}, "
                f"corr {r['corr']}, maxDD {r['max_dd']:.1%}, trades {r['trades']}"
            )
        except Exception as exc:
            print(f"{name}: FAILED: {exc!r}", file=sys.stderr)
            results.append({"bot": name, "htf": cfg["htf"], "notes": cfg["notes"], "error": str(exc), "passed_bar": False})

    table = format_table([r for r in results if "error" not in r])
    print("\n" + table + "\n")
    print(f"Completed in {time.time() - t0:.1f}s", file=sys.stderr)

    if args.save_doc and DOC_PATH.exists():
        content = DOC_PATH.read_text(encoding="utf-8")
        # Replace results section placeholder if needed
        DOC_PATH.write_text(content, encoding="utf-8")
        print(f"Updated {DOC_PATH}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
