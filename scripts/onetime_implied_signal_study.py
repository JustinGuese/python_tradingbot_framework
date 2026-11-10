"""Walk-forward tuning and study of option-implied sentiment signals (Step S1).

Signals investigated:
  1. cp_spread: Cremers-Weinbaum ATM call IV minus ATM put IV (2010).
  2. put_skew: 25-delta put IV minus ATM IV.
  3. rr25: 25-delta call IV minus 25-delta put IV (Risk Reversal).
  4. composite: standardized combination of cp_spread + rr25 - put_skew.

Protocol:
  - Walk-forward tuning using scripts/option_tune_common.py
  - Candidate chosen on H1 (objective: alpha_t vs QQQ), judged on H2.
  - Ship tuned parameters only if H1 winner beats a-priori defaults on H2.
"""

from __future__ import annotations

import argparse
import datetime
from itertools import product
import logging
import os
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd
import scipy.stats as stats

from tradingbot.optionimpliedsignalbot import DEFAULT_UNIVERSE, ImpliedSignalRules
from tradingbot.utils.implied_signal import (
    compute_cross_sectional_signals,
    compute_portfolio_weights,
    compute_snapshot_signals,
    load_signals_from_db,
)
from scripts.option_tune_common import (
    ship_decision,
    summary_table,
    walk_forward,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def compute_ic_table(
    signals_df: pd.DataFrame,
    px_df: pd.DataFrame,
    signal_names: list[str] = ("cp_spread", "put_skew", "rr25", "composite"),
) -> pd.DataFrame:
    """Calculate weekly rank IC and quintile spread for 1w and 4w forward returns."""
    # Compute 1-week and 4-week forward returns from price table
    fwd_ret_1w = px_df.pct_change(1, fill_method=None).shift(-1)
    fwd_ret_4w = px_df.pct_change(4, fill_method=None).shift(-4)

    rows = []

    for sig in signal_names:
        # Cross-sectional signals without smoothing
        enriched = compute_cross_sectional_signals(signals_df, signal_name=sig, smoothing=1)
        sig_pivot = enriched.pivot(index="snapshot_at", columns="underlying", values=sig)

        # Align timestamps
        common_idx = sig_pivot.index.intersection(px_df.index)
        if len(common_idx) < 3:
            continue

        for horizon_name, fwd_df in [("1-week", fwd_ret_1w), ("4-week", fwd_ret_4w)]:
            ic_list = []
            spread_list = []

            for dt in common_idx:
                s_vals = sig_pivot.loc[dt].dropna()
                r_vals = fwd_df.loc[dt].dropna() if dt in fwd_df.index else pd.Series(dtype=float)

                common_syms = s_vals.index.intersection(r_vals.index)
                if len(common_syms) < 5:
                    continue

                x = s_vals.loc[common_syms]
                y = r_vals.loc[common_syms]

                # Spearman Rank IC
                res = stats.spearmanr(x, y)
                ic = getattr(res, "statistic", getattr(res, "correlation", np.nan))
                if np.isfinite(ic):
                    ic_list.append(ic)

                # Quintile spread: Top 20% vs Bottom 20%
                k = max(1, len(common_syms) // 5)
                top_syms = x.nlargest(k).index
                bot_syms = x.nsmallest(k).index
                top_ret = y.loc[top_syms].mean()
                bot_ret = y.loc[bot_syms].mean()
                if np.isfinite(top_ret) and np.isfinite(bot_ret):
                    spread_list.append(top_ret - bot_ret)

            n_obs = len(ic_list)
            if n_obs >= 2:
                mean_ic = float(np.mean(ic_list))
                std_ic = float(np.std(ic_list, ddof=1))
                t_stat = (mean_ic / (std_ic / np.sqrt(n_obs))) if std_ic > 1e-8 else np.nan
                mean_spread = float(np.mean(spread_list)) if spread_list else np.nan
                ann_factor = 52.0 if horizon_name == "1-week" else 13.0
                ann_spread = mean_spread * ann_factor if np.isfinite(mean_spread) else np.nan
            else:
                mean_ic, t_stat, ann_spread = np.nan, np.nan, np.nan

            rows.append(
                {
                    "Signal": sig,
                    "Horizon": horizon_name,
                    "N Obs": n_obs,
                    "Mean Rank IC": f"{mean_ic:+.3f}" if np.isfinite(mean_ic) else "NaN",
                    "IC t-stat": f"{t_stat:+.2f}" if np.isfinite(t_stat) else "NaN",
                    "Q5 - Q1 Ann Spread": f"{ann_spread:+.1%}" if np.isfinite(ann_spread) else "NaN",
                }
            )

    return pd.DataFrame(rows)


def run_strategy_simulation(
    params: dict[str, Any],
    signals_df: pd.DataFrame,
    px_df: pd.DataFrame,
    qqq_px: pd.Series,
) -> tuple[pd.Series, int, pd.Series]:
    """Simulate weekly/biweekly rebalancing equity curve for given hyperparameters."""
    sig_name = params["signal"]
    smoothing = params["smoothing"]
    top_n = params["top_n"]
    rebalance = params["rebalance"]

    enriched = compute_cross_sectional_signals(signals_df, signal_name=sig_name, smoothing=smoothing)
    sig_col = f"{sig_name}_smooth" if smoothing > 1 else sig_name

    snapshots = sorted(enriched["snapshot_at"].unique())
    if len(snapshots) < 2:
        empty = pd.Series([1.0], index=[pd.Timestamp.now()])
        return empty, 0, empty

    # Filter to rebalance dates
    step = 1 if rebalance == "weekly" else 2
    reb_snapshots = set(snapshots[::step])

    # Pre-compute weekly stock returns
    weekly_rets = px_df.pct_change(fill_method=None)
    qqq_rets = qqq_px.pct_change(fill_method=None)

    portfolio_rets = []
    dates = []
    current_weights = pd.Series(dtype=float)
    trade_count = 0

    for i in range(len(snapshots) - 1):
        dt = snapshots[i]
        next_dt = snapshots[i + 1]

        if dt in reb_snapshots or current_weights.empty:
            sub = enriched[enriched["snapshot_at"] == dt].set_index("underlying")
            ranks = sub["rank"]
            current_weights = compute_portfolio_weights(ranks, top_n=top_n)
            trade_count += int((current_weights > 0).sum())

        # Portfolio return from dt to next_dt
        if next_dt in weekly_rets.index:
            r_vec = weekly_rets.loc[next_dt]
            w_aligned = current_weights.reindex(r_vec.index).fillna(0.0)
            port_r = float((w_aligned * r_vec.fillna(0.0)).sum())
        else:
            port_r = 0.0

        portfolio_rets.append(port_r)
        dates.append(next_dt)

    curve = pd.Series(1.0 + np.array(portfolio_rets), index=dates).cumprod()
    bench = (1.0 + qqq_rets.reindex(dates).fillna(0.0)).cumprod()

    return curve, trade_count, bench


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Implied Signal Study and Tuning (Step S1)")
    parser.add_argument(
        "--names",
        type=str,
        default="",
        help="Comma-separated underlyings (default: all single-stock capture universe)",
    )
    parser.add_argument("--start", type=str, default="2019-05-10", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default="2026-11-09", help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--max-grid",
        type=int,
        default=None,
        help="Max number of grid combinations to run (e.g. 4 for smoke test)",
    )
    parser.add_argument(
        "--cache-dir",
        type=str,
        default="/tmp/implied_signal_cache",
        help="Directory to cache fetched option signals",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="docs/backtests/implied-signal-2026-11.md",
        help="Markdown file output path",
    )
    parser.add_argument(
        "--grid-csv",
        type=str,
        default="docs/backtests/implied_signal_grid_2026_11.csv",
        help="CSV file path for full grid results",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Parse underlyings
    if args.names.strip():
        names = [s.strip().upper() for s in args.names.split(",") if s.strip()]
    else:
        names = list(DEFAULT_UNIVERSE)

    # Always ensure QQQ is included for the benchmark
    fetch_names = sorted(set(names) | {"QQQ"})

    start_date = pd.to_datetime(args.start).date()
    end_date = pd.to_datetime(args.end).date()

    logger.info("Loading option signals for %d symbols from %s to %s", len(fetch_names), start_date, end_date)
    signals_df = load_signals_from_db(
        underlyings=fetch_names,
        start=start_date,
        end=end_date,
        cache_dir=args.cache_dir,
        weekly_only=True,
    )

    if signals_df.empty:
        logger.error("No option quotes or signals found for given parameters. Exiting.")
        sys.exit(1)

    logger.info("Loaded %d signal rows across %d snapshots", len(signals_df), signals_df["snapshot_at"].nunique())

    # Build price table
    px_pivot = signals_df.pivot(index="snapshot_at", columns="underlying", values="spot").sort_index()

    if "QQQ" in px_pivot.columns:
        qqq_px = px_pivot["QQQ"].dropna()
    else:
        # Fallback to equal-weight if QQQ missing
        logger.warning("QQQ not found in quotes, using equal-weight universe as proxy benchmark")
        qqq_px = px_pivot.mean(axis=1).dropna()

    # 1. Compute Information Coefficient table
    logger.info("Computing Rank IC and Quintile Spreads...")
    ic_table = compute_ic_table(signals_df, px_pivot)

    # 2. Build parameter grid
    signal_levers = ["composite", "cp_spread", "put_skew", "rr25"]
    smoothing_levers = [1, 5, 10]
    top_n_levers = [3, 5, 8]
    rebalance_levers = ["weekly", "biweekly"]

    defaults_params = {
        "signal": "composite",
        "smoothing": 5,
        "top_n": 5,
        "rebalance": "weekly",
    }

    raw_grid = [
        {"signal": s, "smoothing": sm, "top_n": tn, "rebalance": reb}
        for s, sm, tn, reb in product(signal_levers, smoothing_levers, top_n_levers, rebalance_levers)
    ]

    # Ensure defaults combination is first
    grid = [defaults_params] + [p for p in raw_grid if p != defaults_params]

    if args.max_grid is not None and args.max_grid > 0:
        grid = grid[: args.max_grid]
        logger.info("Capped grid to %d combinations for smoke test", len(grid))
    else:
        logger.info("Running full grid of %d combinations", len(grid))

    # Split date: halfway or 2023-01-01
    snaps = sorted(signals_df["snapshot_at"].unique())
    split_date = snaps[len(snaps) // 2]
    logger.info("Walk-forward split date: %s", split_date)

    def run_fn(combo: dict[str, Any]):
        return run_strategy_simulation(combo, signals_df, px_pivot, qqq_px)

    wf_results = walk_forward(grid, run_fn, split=split_date, rank_key="alpha_t")

    # Add parameter columns to wf_results
    for k in ("signal", "smoothing", "top_n", "rebalance"):
        wf_results[k] = [g[k] for g in grid]

    # Save CSV
    grid_csv_path = Path(args.grid_csv)
    grid_csv_path.parent.mkdir(parents=True, exist_ok=True)
    wf_results.to_csv(grid_csv_path, index=False)
    logger.info("Saved full grid results to %s", grid_csv_path)

    # Summary table
    defaults_row = wf_results.iloc[0]
    h1_col = "H1_t" if "H1_t" in wf_results.columns else "t"
    h1_winner_idx = wf_results[h1_col].astype(float).idxmax()
    h1_winner_row = wf_results.loc[h1_winner_idx]

    summary_df = summary_table(
        wf_results,
        bot_name="option_ImpliedSignalBot",
        defaults_row=defaults_row,
        h1_winner_row=h1_winner_row,
    )

    # Shipped decision
    decision = ship_decision(defaults_row, h1_winner_row)
    shipped_params = h1_winner_row if decision == "winner" else defaults_row

    # Baseline Equal-Weight Universe Curve
    ew_rets = px_pivot.drop(columns=["QQQ"], errors="ignore").pct_change(fill_method=None).mean(axis=1)
    ew_curve = (1.0 + ew_rets.fillna(0.0)).cumprod()
    defaults_curve, _, _ = run_fn(defaults_params)
    winner_curve, _, _ = run_fn(dict(h1_winner_row))

    def _curve_metrics(name: str, c: pd.Series) -> dict[str, Any]:
        from scripts.option_tune_common import compute_max_dd, compute_sharpe, true_cagr
        from tradingbot.utils.alpha_report import alpha_stats

        stats_obj = alpha_stats(c, qqq_px.reindex(c.index).ffill().bfill())
        return {
            "Strategy": name,
            "CAGR": f"{true_cagr(curve=c):.1%}",
            "Sharpe": f"{compute_sharpe(c):.2f}",
            "Max DD": f"{compute_max_dd(c):.1%}",
            "Beta vs QQQ": f"{stats_obj.beta:.2f}" if np.isfinite(stats_obj.beta) else "NaN",
            "Alpha/yr vs QQQ": f"{stats_obj.alpha:.1%}" if np.isfinite(stats_obj.alpha) else "NaN",
            "Alpha t": f"{stats_obj.t:.2f}" if np.isfinite(stats_obj.t) else "NaN",
        }

    perf_df = pd.DataFrame(
        [
            _curve_metrics("Defaults (Composite, 5-sm, Top 5, W)", defaults_curve),
            _curve_metrics(f"H1 Winner ({h1_winner_row.get('signal')}, {h1_winner_row.get('smoothing')}, Top {h1_winner_row.get('top_n')}, {h1_winner_row.get('rebalance')})", winner_curve),
            _curve_metrics("Equal-Weight Universe", ew_curve),
            _curve_metrics("Benchmark QQQ", qqq_px / qqq_px.iloc[0]),
        ]
    )

    # Generate Markdown Report
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    h1_winner_clean = {k: h1_winner_row[k] for k in ("signal", "smoothing", "top_n", "rebalance") if k in h1_winner_row}

    markdown_content = f"""# Option-Implied Sentiment Signal Study (2026-11)

**Primary verdict: `{ 'pass' if decision == 'winner' or float(defaults_row.get('H2_t', 0)) >= 2.0 else 'unproven' }`**.
Generated by `scripts/onetime_implied_signal_study.py`.

## Overview & Methodology
Option prices and implied volatilities embed informed directional beliefs before they appear in equity spot markets.
This study implements and evaluates four option-implied sentiment signals:
1. **`cp_spread`**: Cremers & Weinbaum (2010) call-put mid IV deviation ($IV_{{C, ATM}} - IV_{{P, ATM}}$). Deviations from put-call parity predict stock returns.
2. **`put_skew`**: 25-delta put IV minus ATM IV ($IV_{{P, 25\\Delta}} - IV_{{ATM}}$). Captures tail crash insurance demand.
3. **`rr25`**: 25-delta risk reversal ($IV_{{C, 25\\Delta}} - IV_{{P, 25\\Delta}}$). Net skew between upside calls and downside puts.
4. **`composite`**: Cross-sectionally standardized combination ($z(cp\\_spread) + z(rr25) - z(put\\_skew)$).

### Data & Sample
- Table: `option_quotes` (~52 underlyings, sampled snapshots from {start_date} to {end_date}).
- Target: 30 DTE options, weekly Friday snapshot near close.
- Number of underlyings evaluated: {len(names)}
- Split Date: {split_date}

## 1. Information Coefficient & Quintile Spreads
Spearman Rank IC of signals against forward equity returns, plus top-minus-bottom quintile spread:

{ic_table.to_markdown(index=False)}

## 2. Walk-Forward Grid Tuning Summary
Protocol: Parameter combination chosen on H1 ({start_date} to {str(split_date)[:10]}), evaluated out-of-sample on H2 ({str(split_date)[:10]} to {end_date}).

{summary_df.to_markdown(index=False)}

*Full grid results ({len(wf_results)} combinations) exported to `{grid_csv_path}`.*

## 3. Full-Window Strategy Performance
Long-only portfolio holding the Top-N names, rebalanced weekly/biweekly:

{perf_df.to_markdown(index=False)}

## Reading the Results
- **Sample observations**: Independent weekly periods: ~{len(snaps)} weeks across {len(names)} stocks.
- **H1 Winner vs Defaults**: H1 winner selected parameters (`{h1_winner_clean}`) vs Defaults (`{defaults_params}`).
- **Shipment Decision**: `{decision}`. The bot configuration `{shipped_params.get('signal')}` with smoothing `{shipped_params.get('smoothing')}`, top `{shipped_params.get('top_n')}`, and `{shipped_params.get('rebalance')}` rebalance is deployed.
"""

    out_path.write_text(markdown_content)
    logger.info("Report written to %s", out_path)
    print("\n--- Summary Table ---")
    print(summary_df.to_string())
    print("\n--- Performance Table ---")
    print(perf_df.to_string())


if __name__ == "__main__":
    main()
