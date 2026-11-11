"""Study and walk-forward tuning of CrossVol ranking filters & structure variants (C1).

Evaluates:
  1. Candidate ranks: gap (baseline), vol-of-vol, idiosyncratic vol.
  2. Structure variants: default iron condor vs delta-hedged call writing (Han et al.).
  3. Walk-forward protocol: pick on H1 alpha t vs QQQ, judge on H2.

Loads option data per underlying over the date range with local parquet caching.
Resumable via --cache-dir.
"""

from __future__ import annotations

import argparse
import itertools
import logging
import math
import os
import pickle
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

import pandas as pd

# Ensure repo and scripts directories are in sys.path
CWD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if CWD not in sys.path:
    sys.path.insert(0, CWD)
SCRIPTS_DIR = os.path.join(CWD, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from tradingbot.option_crossvolbot import UNIVERSE  # noqa: E402
from tradingbot.utils.backtest import _compute_alpha_metrics  # noqa: E402
from tradingbot.utils.crossvol_rank import make_crossvol_decide  # noqa: E402
from tradingbot.utils.db import OptionQuote, get_db_session  # noqa: E402
from tradingbot.utils.option_replay import (  # noqa: E402
    IRX,
    PriceHistory,
    ReplayMarket,
    run_strategy,
)
from tradingbot.utils.option_rules import CrossVolRules  # noqa: E402
from tradingbot.utils.vol_indices import VIX, VIX3M, VVIX  # noqa: E402

logger = logging.getLogger(__name__)

TABLE_HEADER = (
    "| Configuration | Window | CAGR | Sharpe | Alpha/yr | t | Beta | Corr | Max DD | Trades |\n|" + "---|" * 10
)


def compute_metrics(curve: pd.Series, qqq: pd.Series) -> dict:
    if len(curve) < 2:
        return {"cagr": 0.0, "sharpe": 0.0, "alpha": None, "t": None, "beta": None, "corr": None, "max_dd": 0.0}
    a = _compute_alpha_metrics(list(curve.values), list(curve.index), qqq, "1d")
    years = max((curve.index[-1] - curve.index[0]).days / 365.25, 1e-4)
    cagr = (curve.iloc[-1] / max(curve.iloc[0], 1e-4)) ** (1 / years) - 1
    rets = curve.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * math.sqrt(252)) if len(rets) > 1 and rets.std() > 1e-12 else 0.0
    cummax = curve.cummax()
    max_dd = float((curve / cummax - 1).min()) if (cummax > 0).all() else 0.0
    return {
        "cagr": float(cagr),
        "sharpe": sharpe,
        "alpha": a.get("alpha"),
        "t": a.get("alpha_t"),
        "beta": a.get("beta"),
        "corr": a.get("benchmark_corr"),
        "max_dd": max_dd,
    }


def format_row(name: str, window: str, m: dict, trades="") -> str:
    sharpe = f"{m['sharpe']:.2f}" if m.get("sharpe") is not None else "N/A"
    alpha = f"{m['alpha']:+.1%}" if m.get("alpha") is not None else "N/A"
    t_val = f"{m['t']:.2f}" if m.get("t") is not None else "N/A"
    beta = f"{m['beta']:.2f}" if m.get("beta") is not None else "N/A"
    corr = f"{m['corr']:.2f}" if m.get("corr") is not None else "N/A"
    return (
        f"| {name} | {window} | {m.get('cagr', 0.0):+.1%} | {sharpe} | {alpha} | "
        f"{t_val} | {beta} | {corr} | {m.get('max_dd', 0.0):.1%} | {trades} |"
    )


def load_replay_market(
    start: date,
    end: date,
    underlyings: Sequence[str],
    cache_dir: str,
) -> ReplayMarket:
    """
    Load option chains with per-underlying parquet caching.
    Always filters by underlying and snapshot_at date range to avoid full-table scans.
    """
    os.makedirs(cache_dir, exist_ok=True)
    cols = [
        OptionQuote.underlying,
        OptionQuote.contract_symbol,
        OptionQuote.expiration,
        OptionQuote.option_type,
        OptionQuote.strike,
        OptionQuote.bid,
        OptionQuote.ask,
        OptionQuote.last_price,
        OptionQuote.volume,
        OptionQuote.open_interest,
        OptionQuote.implied_volatility,
        OptionQuote.snapshot_at,
        OptionQuote.created_at,
        OptionQuote.underlying_price,
    ]
    col_names = [c.name for c in cols]
    parts: list[pd.DataFrame] = []

    for u in sorted(set(underlyings)):
        cache_file = os.path.join(cache_dir, f"quotes_{u}_{start}_{end}.pkl")
        if os.path.exists(cache_file):
            logger.info("Loading %s from pickle cache: %s", u, cache_file)
            with open(cache_file, "rb") as f:
                df_u = pickle.load(f)
        else:
            logger.info("Querying DB for %s between %s and %s", u, start, end)
            start_dt = datetime.combine(start, datetime.min.time(), UTC)
            end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time(), UTC)
            with get_db_session() as session:
                rows = (
                    session.query(*cols)
                    .filter(
                        OptionQuote.underlying == u,
                        OptionQuote.snapshot_at >= start_dt,
                        OptionQuote.snapshot_at < end_dt,
                    )
                    .all()
                )
            df_u = pd.DataFrame(rows, columns=col_names) if rows else pd.DataFrame(columns=col_names)
            with open(cache_file, "wb") as f:
                pickle.dump(df_u, f)
            logger.info("Saved %d rows for %s to %s", len(df_u), u, cache_file)
        if not df_u.empty:
            parts.append(df_u)

    full_frame = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=col_names)

    market = ReplayMarket.__new__(ReplayMarket)
    market._events = {}
    market._macro = None
    market._derived = {}
    market.caps = None
    market._spots = {}

    if full_frame.empty:
        market.frame = full_frame
        market._by_day = {}
        return market

    full_frame["day"] = pd.to_datetime(full_frame["snapshot_at"]).dt.date
    full_frame["expiry"] = pd.to_datetime(full_frame["expiration"]).dt.date
    full_frame = full_frame.sort_values("snapshot_at").drop_duplicates(["contract_symbol", "day"], keep="last")
    market.frame = full_frame
    market._by_day = {k: g.reset_index(drop=True) for k, g in full_frame.groupby(["underlying", "day"])}
    px = full_frame.assign(px=pd.to_numeric(full_frame["underlying_price"], errors="coerce")).dropna(subset=["px"])
    for u, g in px.groupby("underlying"):
        market._spots[u] = g.groupby("day")["px"].last().sort_index()

    return market


def load_price_history(symbols: Sequence[str], start: date, cache_dir: str) -> PriceHistory:
    """Download and cache daily OHLC price history."""
    os.makedirs(cache_dir, exist_ok=True)
    sym_hash = abs(hash(tuple(sorted(symbols)))) % 10000000
    cache_file = os.path.join(cache_dir, f"prices_{sym_hash}_{start}.pkl")
    if os.path.exists(cache_file):
        with open(cache_file, "rb") as f:
            return pickle.load(f)
    prices = PriceHistory.download(set(symbols) | {"QQQ", VIX, VIX3M, VVIX, IRX}, start)
    with open(cache_file, "wb") as f:
        pickle.dump(prices, f)
    return prices


def build_tuning_grid() -> list[CrossVolRules]:
    """Economically motivated tuning grid, <= 300 combinations."""
    combos = []
    # Always include the a-priori default baseline first
    combos.append(CrossVolRules(rank_metric=None, structure=None))

    ranks = ["gap", "vol_of_vol", "idio_vol"]
    structures = ["condor", "delta_hedged_call"]
    short_deltas = [0.10, 0.15]
    target_dtes = [21, 35]
    min_gaps = [0.03, 0.05]
    stop_losses = [1.5, 2.0]

    for r, st, d, dte, g, sl in itertools.product(ranks, structures, short_deltas, target_dtes, min_gaps, stop_losses):
        combos.append(
            CrossVolRules(
                rank_metric=r,
                structure=st,
                short_delta=d,
                target_dte=dte,
                min_gap=g,
                stop_loss=sl,
            )
        )
    return combos


def run_evaluation(
    rules: CrossVolRules,
    market: ReplayMarket,
    prices: PriceHistory,
    underlyings: Sequence[str],
    fill: str,
) -> dict:
    decide = make_crossvol_decide(rules, underlyings)
    curve, book = run_strategy(market, decide, prices, fill=fill)
    if curve.empty or len(curve) < 2:
        empty_m = {"cagr": 0.0, "sharpe": 0.0, "alpha": 0.0, "t": -99.0, "beta": 0.0, "corr": 0.0, "max_dd": 0.0}
        return {"rules": rules, "trades": 0, "full": empty_m, "H1": empty_m, "H2": empty_m}
    days = market.days
    qqq = prices.upto("QQQ", days[-1])["close"]
    split = curve.index[len(curve) // 2]
    return {
        "rules": rules,
        "trades": book.trades,
        "full": compute_metrics(curve, qqq),
        "H1": compute_metrics(curve.loc[:split], qqq),
        "H2": compute_metrics(curve.loc[split:], qqq),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="CrossVol ranking study and walk-forward backtest")
    ap.add_argument("--names", help="Comma-separated symbols (default: CrossVol universe)")
    ap.add_argument("--start", default="2019-05-10", help="Start date (YYYY-MM-DD)")
    ap.add_argument("--end", default="2026-11-09", help="End date (YYYY-MM-DD)")
    ap.add_argument("--max-grid", type=int, default=None, help="Maximum grid combinations to evaluate")
    ap.add_argument("--cache-dir", default="/tmp/crossvol_cache", help="Local parquet/pickle cache directory")
    ap.add_argument("--fill", choices=("quote", "mid"), default="quote", help="Fill mode")
    ap.add_argument("--out", default="docs/backtests/crossvol-rank-2026-11.md", help="Markdown report output path")
    ap.add_argument("--csv", default="docs/backtests/crossvol-rank-2026-11.csv", help="Full grid CSV output path")
    args = ap.parse_args()

    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)
    names = args.names.split(",") if args.names else list(UNIVERSE)

    print(f"Loading replay market for {len(names)} underlyings from {start_date} to {end_date}...")
    market = load_replay_market(start_date, end_date, names, args.cache_dir)
    days = market.days
    print(f"Loaded {len(days)} trading days ({days[0] if days else '-'} -> {days[-1] if days else '-'}).")

    if not days:
        print("No option quote data found for the given parameters.")
        return

    prices = load_price_history(names, days[0], args.cache_dir)

    grid = build_tuning_grid()
    if args.max_grid is not None:
        grid = grid[: args.max_grid]

    print(f"Evaluating {len(grid)} grid configurations...")
    results = []
    for idx, rules in enumerate(grid, 1):
        desc = f"metric={rules.rank_metric or 'gap'}, struct={rules.structure or 'condor'}, dte={rules.target_dte}, delta={rules.short_delta}"
        print(f"[{idx}/{len(grid)}] Evaluating: {desc}")
        try:
            res = run_evaluation(rules, market, prices, names, args.fill)
        except (ValueError, ZeroDivisionError) as exc:  # e.g. NaN risk budget on an uncapped-loss structure
            print(f"       -> skipped ({type(exc).__name__}: {exc})")
            continue
        results.append(res)
        h1_t = res["H1"]["t"]
        h2_t = res["H2"]["t"]
        print(
            f"       -> H1 alpha t: {h1_t if h1_t is not None else 0.0:.2f}, H2 alpha t: {h2_t if h2_t is not None else 0.0:.2f}, Trades: {res['trades']}"
        )

    # Baseline is first
    baseline = results[0]

    # Save CSV of full grid
    csv_rows = []
    for r in results:
        rl = r["rules"]
        csv_rows.append(
            {
                "rank_metric": rl.rank_metric or "gap",
                "structure": rl.structure or "condor",
                "target_dte": rl.target_dte,
                "short_delta": rl.short_delta,
                "min_gap": rl.min_gap,
                "stop_loss": rl.stop_loss,
                "h1_cagr": r["H1"]["cagr"],
                "h1_sharpe": r["H1"]["sharpe"],
                "h1_alpha": r["H1"]["alpha"],
                "h1_t": r["H1"]["t"],
                "h1_beta": r["H1"]["beta"],
                "h1_corr": r["H1"]["corr"],
                "h1_max_dd": r["H1"]["max_dd"],
                "h2_cagr": r["H2"]["cagr"],
                "h2_sharpe": r["H2"]["sharpe"],
                "h2_alpha": r["H2"]["alpha"],
                "h2_t": r["H2"]["t"],
                "h2_beta": r["H2"]["beta"],
                "h2_corr": r["H2"]["corr"],
                "h2_max_dd": r["H2"]["max_dd"],
                "full_cagr": r["full"]["cagr"],
                "full_sharpe": r["full"]["sharpe"],
                "full_alpha": r["full"]["alpha"],
                "full_t": r["full"]["t"],
                "trades": r["trades"],
            }
        )
    pd.DataFrame(csv_rows).to_csv(args.csv, index=False)
    print(f"Full grid results written to {args.csv}")

    # Build summary comparison table
    ranked_by_h1 = sorted(results, key=lambda x: x["H1"]["t"] if x["H1"]["t"] is not None else -99.0, reverse=True)
    h1_winner = ranked_by_h1[0]

    summary_lines = [TABLE_HEADER]
    summary_lines.append(format_row("Live defaults (baseline)", "H1", baseline["H1"], baseline["trades"]))
    summary_lines.append(format_row("Live defaults (baseline)", "H2", baseline["H2"]))
    summary_lines.append(format_row("Live defaults (baseline)", "full", baseline["full"]))

    winner_desc = f"H1 Winner ({h1_winner['rules'].rank_metric or 'gap'}, {h1_winner['rules'].structure or 'condor'}, dte={h1_winner['rules'].target_dte})"
    summary_lines.append(format_row(winner_desc, "H1", h1_winner["H1"], h1_winner["trades"]))
    summary_lines.append(format_row(winner_desc, "H2", h1_winner["H2"]))
    summary_lines.append(format_row(winner_desc, "full", h1_winner["full"]))
    summary_table_md = "\n".join(summary_lines)

    # Build grid table
    grid_lines = [TABLE_HEADER]
    for i, res in enumerate(ranked_by_h1[:15], 1):
        desc = f"#{i} {res['rules'].rank_metric or 'gap'}, {res['rules'].structure or 'condor'}, dte={res['rules'].target_dte}, d={res['rules'].short_delta}"
        grid_lines.append(format_row(desc, "H1", res["H1"], res["trades"]))
        grid_lines.append(format_row(desc, "H2", res["H2"]))
    grid_table_md = "\n".join(grid_lines)

    # Verdict
    b_h2_t = baseline["H2"]["t"] or 0.0
    w_h2_t = h1_winner["H2"]["t"] or 0.0
    passed_kill_gate = (w_h2_t > 0.0) and (w_h2_t > b_h2_t)

    if passed_kill_gate:
        verdict_md = (
            f"**Verdict: PASSED.** The H1 winning configuration turned H2 alpha t to {w_h2_t:.2f} (> 0) "
            f"and beat baseline H2 alpha t ({b_h2_t:.2f}). Rules can be updated."
        )
    else:
        verdict_md = (
            f"**Verdict: KILLED / REMAINS PAUSED.** The H1 winning configuration achieved H2 alpha t of {w_h2_t:.2f} "
            f"(baseline H2 alpha t: {b_h2_t:.2f}). It failed to produce positive out-of-sample alpha t vs QQQ. "
            f"In accordance with PLAN.md step C1, no rule changes are shipped to production, and `option_CrossVolBot` "
            f"remains paused (`suspend: true`)."
        )

    # Write or update --out file
    doc_template = r"""# CrossVol Ranking Filters & Structure Comparison (2026-11)

## Context & Hypotheses

CrossVol (`option_CrossVolBot`) previously evaluated on 25 real names scored alpha −10%/yr with t = −3.56 and was paused (`values.yaml`, `suspend: true`). As noted in PLAN.md step C1, this study is a kill-gate, not a rescue.

The study tests three candidate ranking filters across single-stock options:
1. **Implied - Realized Vol Gap (`gap`)**: The a-priori baseline ($IV - HAR_{fair}$), selling condors on the single names whose implied volatility sits furthest above their forecast volatility.
2. **Vol-of-Vol (`vol_of_vol`)**: Sample standard deviation of daily ATM IV changes ($\sigma(\Delta IV_t)$ over a rolling window).
3. **Idiosyncratic Volatility (`idio_vol`)**: Annualized standard deviation of residuals from stock returns regressed against QQQ ($R_{i,t} = \alpha + \beta R_{m,t} + \epsilon_{i,t}$).

Additionally, we evaluate:
- **Delta-Hedged Call Writing (Han et al. comparison)**: Selling call spreads while dynamically delta-hedging the underlying stock.

## Walk-Forward Protocol

- **Objective**: Walk-forward alpha t vs QQQ (not raw Sharpe or CAGR, to avoid beta-chasing).
- **Split**: 50/50 temporal split (H1 / H2).
- **Rule**: Pick best configuration on H1 alpha t; judge on H2. Ship tuned values in `CrossVolRules` only if the H1 winner turns H2 alpha t > 0 **AND** beats the live baseline on H2. Otherwise, defaults remain and the bot stays paused.

## Baseline vs Candidate Ranking Filters

<!-- SUMMARY_TABLE -->

## Full Tuning Grid Results

<!-- GRID_TABLE -->

## Kill-Gate Verdict

<!-- VERDICT -->
"""
    if os.path.exists(args.out):
        with open(args.out) as f:
            content = f.read()
        if "<!-- SUMMARY_TABLE -->" not in content:
            content = doc_template
    else:
        content = doc_template

    content = content.replace("<!-- SUMMARY_TABLE -->", summary_table_md)
    content = content.replace("<!-- GRID_TABLE -->", grid_table_md)
    content = content.replace("<!-- VERDICT -->", verdict_md)

    with open(args.out, "w") as f:
        f.write(content)
    print(f"Updated markdown report written to {args.out}")


if __name__ == "__main__":
    main()
