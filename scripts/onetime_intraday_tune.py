"""
Walk-forward grid for the ORB and VWAP-pullback rules, pooled over liquid tickers.

60 days of 5m bars on QQQ alone is ~40 trades: any argmax is noise. So the sample is
widened across tickers (correlated, so the effective n is less than it looks) and the
parameters are chosen on the FIRST half of the sessions only, then judged on the second.
A cell counts only if its net mean trade return is positive in both halves.

Usage: PYTHONPATH=. uv run python scripts/onetime_intraday_tune.py
"""

from __future__ import annotations

import itertools
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import Parallel, delayed

sys.path.insert(0, str(Path(__file__).parent))
from onetime_intraday_orb_vwap_backtest import fetch_5m, run_rules, split_sessions, trade_return

from tradingbot.utils.orb_rules import ORBRules
from tradingbot.utils.vwap_pullback_rules import VWAPPullbackRules

UNIVERSE = ["QQQ", "SPY", "IWM", "DIA", "XLK", "SMH", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META"]
COSTS = {"1bp": 0.0001, "5bp": 0.0005}

GRIDS = {
    "ORB": (
        ORBRules,
        {
            "or_minutes": [5, 15, 30],
            "target_r": [0.35, 0.5, 1.0, 1.5, 2.0, 3.0],
            "window_minutes": [60, 90, 150],
            "max_stop_pct": [0.003, 0.006, 0.01],
        },
    ),
    "VWAP": (
        VWAPPullbackRules,
        {
            "or_minutes": [5, 15, 30],
            "swing_bars": [3, 6, 9, 12],
            "target_r": [1.0, 1.5, 2.0, 3.0],
            "pullback_tol": [0.0003, 0.001],
            "min_risk_pct": [0.0005, 0.001],
        },
    ),
}


def cell_stats(trades_by_sym: dict, days: list, slip: float) -> dict:
    """Pooled net per-trade return stats over `days` for one parameter cell."""
    dayset = set(days)
    rets, per_sym = [], {}
    for sym, trades in trades_by_sym.items():
        r = [trade_return(t, slip) for d, t in trades.items() if t is not None and d in dayset]
        per_sym[sym] = float(np.mean(r)) if r else 0.0
        rets += r
    a = np.array(rets)
    n = len(a)
    mean = float(a.mean()) if n else 0.0
    t = float(mean / a.std(ddof=1) * np.sqrt(n)) if n > 2 and a.std(ddof=1) > 0 else 0.0
    return {
        "n": n,
        "mean_bps": mean * 1e4,
        "t": t,
        "win": float((a > 0).mean()) if n else 0.0,
        "syms_pos": sum(v > 0 for v in per_sym.values()),
    }


def evaluate(cls, params: dict, sess_by_sym: dict, first: list, second: list) -> dict:
    rules = cls(**params)
    trades = {s: run_rules(ss, rules) for s, ss in sess_by_sym.items()}
    row = dict(params)
    for tag, slip in COSTS.items():
        a, b = cell_stats(trades, first, slip), cell_stats(trades, second, slip)
        row.update(
            {
                f"{tag}_h1_bps": a["mean_bps"],
                f"{tag}_h1_t": a["t"],
                f"{tag}_h2_bps": b["mean_bps"],
                f"{tag}_h2_t": b["t"],
                f"{tag}_h2_n": b["n"],
                f"{tag}_h2_syms": b["syms_pos"],
            }
        )
    return row


def main() -> None:
    sess_by_sym = {}
    for sym in UNIVERSE:
        try:
            sess_by_sym[sym] = split_sessions(fetch_5m(sym))
        except Exception as e:
            print(f"skip {sym}: {e}")
    days = sorted(set.intersection(*[set(s) for s in sess_by_sym.values()]))
    first, second = days[: len(days) // 2], days[len(days) // 2 :]
    print(f"{len(sess_by_sym)} symbols, {len(days)} common sessions, {days[0]}..{days[-1]}\n")

    for name, (cls, grid) in GRIDS.items():
        keys = list(grid)
        combos = [dict(zip(keys, v, strict=True)) for v in itertools.product(*grid.values())]
        rows = Parallel(n_jobs=-1)(delayed(evaluate)(cls, c, sess_by_sym, first, second) for c in combos)
        df = pd.DataFrame(rows)
        df.to_csv(f"/tmp/claude-1000/intraday_tune_{name}.csv", index=False)
        default = evaluate(cls, {}, sess_by_sym, first, second)
        print(f"=== {name}: {len(df)} cells ===")
        cols = [*keys, "1bp_h1_bps", "1bp_h1_t", "1bp_h2_bps", "1bp_h2_t", "1bp_h2_n", "5bp_h1_bps", "5bp_h2_bps"]
        print(
            f"defaults (1bp): h1 {default['1bp_h1_bps']:+.1f}bps  h2 {default['1bp_h2_bps']:+.1f}bps t {default['1bp_h2_t']:.2f};"
            f" (5bp): h1 {default['5bp_h1_bps']:+.1f}  h2 {default['5bp_h2_bps']:+.1f}"
        )
        print(
            f"share of cells net-positive at 1bp: h1 {(df['1bp_h1_bps'] > 0).mean():.0%}, h2 {(df['1bp_h2_bps'] > 0).mean():.0%};"
            f" at 5bp: h1 {(df['5bp_h1_bps'] > 0).mean():.0%}, h2 {(df['5bp_h2_bps'] > 0).mean():.0%}"
        )
        # Select on H1 only (1bp), judge on H2.
        top = df.sort_values("1bp_h1_t", ascending=False).head(10)
        print("\ntop 10 by H1 t at 1bp, with their out-of-sample H2:")
        print(top[cols].round(4).to_string(index=False))
        pick = df[df["1bp_h1_bps"] > 0].sort_values("1bp_h1_t", ascending=False).head(10)
        print(
            f"\nmean H2 of the H1-top-10 (1bp): {top['1bp_h2_bps'].mean():+.1f} bps/trade;"
            f" all-cells mean H2: {df['1bp_h2_bps'].mean():+.1f}\n"
        )
        _ = pick, replace


if __name__ == "__main__":
    main()
