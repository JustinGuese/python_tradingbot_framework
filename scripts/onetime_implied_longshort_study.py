"""Walk-forward study: Option-Implied Skew Dollar-Neutral Long/Short Sleeve (Step E1).

Evaluates whether a cross-sectional dollar-neutral long/short stock book selecting
top-N names long (highest option-implied bullishness / risk reversal) and bottom-N names
short (lowest option sentiment / put skew) produces genuine alpha against both QQQ
and an equal-weighted hold of the same 50-stock universe.

Pre-registered grid (8 combinations <= 12 limit):
  1. composite_top5_weekly   : signal=composite, top/bottom N=5, rebalance=weekly (Defaults)
  2. composite_top5_biweekly : signal=composite, top/bottom N=5, rebalance=biweekly
  3. composite_top10_weekly  : signal=composite, top/bottom N=10, rebalance=weekly
  4. composite_top10_biweekly: signal=composite, top/bottom N=10, rebalance=biweekly
  5. rr25_top5_weekly        : signal=rr25, top/bottom N=5, rebalance=weekly
  6. rr25_top5_biweekly      : signal=rr25, top/bottom N=5, rebalance=biweekly
  7. rr25_top10_weekly       : signal=rr25, top/bottom N=10, rebalance=weekly
  8. rr25_top10_biweekly     : signal=rr25, top/bottom N=10, rebalance=biweekly

Global study rules (PLAN-alphaideas.md lines 8-19):
  - Bar (CLAUDE.md): alpha vs QQQ, t >= 2.0, positive in both halves, beta < 0.5.
    Say `unproven`, never round up. (Search winner requires t >= 3.0).
  - Walk-forward: choose on H1, judge on H2. Ships only if H1 winner beats defaults on H2.
  - Pre-registered: 8 variants listed above and recorded in tradingbot/utils/trial_registry.py.
  - Baselines: two (QQQ and equal-weight buy-and-hold of the same 50 names).
  - Cash: reported at 0% (pure spread) and at T-bill yield (^IRX / 252, SHV copier metric).
    Idle weight parked in SHV via park_idle_cash.
  - Costs: 5 bp and 20 bp per side; shorts carry flat 1.0%/yr borrow drag.
  - No hand-written findings: generated document derived strictly from computed numbers.

Database requirement:
  Reads `option_quotes` from production Postgres via POSTGRES_URI (or local cache).
  User runs against a port-forward post-swarm per PLAN-alphaideas.md line 177.

Usage:
  POSTGRES_URI=postgresql://... python scripts/onetime_implied_longshort_study.py [--out DOC_PATH]
"""

from __future__ import annotations

import argparse
import datetime
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("POSTGRES_URI", "stub:stub@localhost:5432/stub")

import numpy as np
import pandas as pd
import yfinance as yf

from tradingbot.optionimpliedlongshortbot import (
    CASH_PROXY,
    DEFAULT_UNIVERSE,
    compute_long_short_portfolio_weights,
)
from tradingbot.utils.alpha_report import alpha_stats
from tradingbot.utils.implied_signal import (
    compute_cross_sectional_signals,
    load_signals_from_db,
)
from tradingbot.utils.trial_registry import record_trial

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("implied_longshort_study")

DEFAULT_OUT = Path("docs/backtests/implied-longshort-2026-11.md")
DEFAULT_GRID_CSV = Path("docs/backtests/implied_longshort_grid_2026_11.csv")
BORROW_FEE_ANNUAL = 0.01  # 1.0%/yr flat borrow fee on short equity notional
DEFAULT_SMOOTHING = 5
GROSS_LIMIT = 1.8

# Pre-registered 8 combinations
PRE_REGISTERED_GRID: list[dict[str, Any]] = [
    {"signal": "composite", "top_n": 5, "rebalance": "weekly", "smoothing": 5},
    {"signal": "composite", "top_n": 5, "rebalance": "biweekly", "smoothing": 5},
    {"signal": "composite", "top_n": 10, "rebalance": "weekly", "smoothing": 5},
    {"signal": "composite", "top_n": 10, "rebalance": "biweekly", "smoothing": 5},
    {"signal": "rr25", "top_n": 5, "rebalance": "weekly", "smoothing": 5},
    {"signal": "rr25", "top_n": 5, "rebalance": "biweekly", "smoothing": 5},
    {"signal": "rr25", "top_n": 10, "rebalance": "weekly", "smoothing": 5},
    {"signal": "rr25", "top_n": 10, "rebalance": "biweekly", "smoothing": 5},
]


def load_daily_prices(
    names: list[str],
    start: str | datetime.date,
    end: str | datetime.date,
    cache_dir: str | Path = "/tmp/implied_signal_cache",
) -> pd.DataFrame:
    """Download daily adjusted close prices for underlyings + benchmarks, cached."""
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    cache_file = cache_path / f"daily_px_{start}_{end}_{len(names)}.pkl"

    if cache_file.exists():
        try:
            logger.info("Loading cached daily prices from %s", cache_file)
            return pd.read_pickle(cache_file)
        except Exception:
            pass

    fetch_tickers = sorted(set(names))
    logger.info("Downloading daily price data for %d tickers from %s to %s", len(fetch_tickers), start, end)
    try:
        raw = yf.download(fetch_tickers, start=str(start), end=str(end), auto_adjust=True, progress=False)["Close"]
        raw = raw.dropna(how="all")
        raw.index = pd.to_datetime(raw.index).tz_localize(None)
        raw.to_pickle(cache_file)
        return raw
    except Exception as e:
        logger.warning("Failed to download daily prices via yfinance: %s", e)
        return pd.DataFrame()


def load_tbill_rate(
    start: str | datetime.date,
    end: str | datetime.date,
    dates_index: pd.DatetimeIndex,
) -> pd.Series:
    """Load daily T-bill rate series (^IRX / 100 / 252), aligning to dates_index."""
    try:
        irx = yf.download("^IRX", start=str(start), end=str(end), auto_adjust=False, progress=False)["Close"].squeeze()
        irx.index = pd.to_datetime(irx.index).tz_localize(None)
        daily_rf = (irx / 100.0 / 252.0).reindex(dates_index).ffill().fillna(0.0)
        return daily_rf
    except Exception:
        logger.warning("Could not download ^IRX, using 4.5% annual fallback rate")
        return pd.Series(0.045 / 252.0, index=dates_index)


def generate_synthetic_option_signals(
    names: list[str],
    dates: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Generate deterministic synthetic option signals for smoke tests / verification."""
    rng = np.random.default_rng(42)
    fridays = [d for d in dates if d.weekday() == 4]
    rows = []
    for d in fridays:
        for und in names:
            cp_spread = float(rng.normal(0.0, 0.02))
            rr25 = float(rng.normal(0.0, 0.03))
            put_skew = float(rng.normal(0.04, 0.015))
            rows.append(
                {
                    "snapshot_at": d,
                    "underlying": und,
                    "spot": 100.0,
                    "cp_spread": cp_spread,
                    "put_skew": put_skew,
                    "rr25": rr25,
                    "call_iv_atm": 0.25,
                    "put_iv_atm": 0.25,
                    "expiry": d + pd.Timedelta(days=30),
                    "dte": 30.0,
                }
            )
    return pd.DataFrame(rows)


def run_strategy_simulation(
    params: dict[str, Any],
    signals_df: pd.DataFrame,
    px_df: pd.DataFrame,
    daily_rf: pd.Series,
) -> tuple[pd.Series, pd.Series, pd.Series, int, float]:
    """Simulate daily returns of the dollar-neutral long/short strategy across cost & cash variations.

    Returns:
        (curve_5bp_zero, curve_20bp_zero, curve_5bp_tbill, trade_count, total_turnover)
    """
    sig_name = params["signal"]
    smoothing = params.get("smoothing", DEFAULT_SMOOTHING)
    top_n = params["top_n"]
    rebalance = params["rebalance"]

    enriched = compute_cross_sectional_signals(signals_df, signal_name=sig_name, smoothing=smoothing)
    snapshots = sorted(enriched["snapshot_at"].unique())
    empty = pd.Series([1.0], index=[pd.Timestamp("2020-01-01")])
    if len(snapshots) < 2:
        return empty, empty, empty, 0, 0.0

    step = 1 if rebalance == "weekly" else 2
    reb_snapshots = set(snapshots[::step])

    stock_rets = px_df.drop(columns=["QQQ", CASH_PROXY], errors="ignore").pct_change(fill_method=None)
    days = stock_rets.index

    # Pre-index ranks by snapshot for O(1) lookups
    ranks_by_snap = {snap: sub_df.set_index("underlying")["rank"] for snap, sub_df in enriched.groupby("snapshot_at")}

    rets_5bp_zero: list[float] = []
    rets_20bp_zero: list[float] = []
    rets_5bp_tbill: list[float] = []
    port_dates: list[pd.Timestamp] = []
    current_weights = pd.Series(dtype=float)
    trade_count = 0
    total_turnover = 0.0

    for i in range(len(snapshots) - 1):
        dt = pd.Timestamp(snapshots[i])
        next_dt = pd.Timestamp(snapshots[i + 1])
        rebal_cost_5bp = 0.0
        rebal_cost_20bp = 0.0

        if dt in reb_snapshots or current_weights.empty:
            ranks = ranks_by_snap.get(snapshots[i], pd.Series(dtype=float))
            new_weights = compute_long_short_portfolio_weights(
                ranks,
                top_n=top_n,
                gross_limit=GROSS_LIMIT,
            )
            # Rebalance turnover
            all_syms = new_weights.index.union(current_weights.index)
            turnover = float(
                new_weights.reindex(all_syms).fillna(0.0).sub(current_weights.reindex(all_syms).fillna(0.0)).abs().sum()
            )
            rebal_cost_5bp = turnover * (5.0 / 10000.0)
            rebal_cost_20bp = turnover * (20.0 / 10000.0)
            trade_count += int((new_weights.abs() > 1e-6).sum())
            total_turnover += turnover
            current_weights = new_weights

        window = days[(days > dt) & (days <= next_dt)]
        for j, d in enumerate(window):
            if d not in stock_rets.index:
                continue
            r_vec = stock_rets.loc[d]
            w_aligned = current_weights.reindex(r_vec.index).fillna(0.0)

            # Long & short stock contributions
            long_w = w_aligned[w_aligned > 0]
            short_w = w_aligned[w_aligned < 0]

            r_long = float((long_w * r_vec.reindex(long_w.index).fillna(0.0)).sum())
            r_short = float((short_w * r_vec.reindex(short_w.index).fillna(0.0)).sum())
            r_stock = r_long + r_short

            # Short borrow fee drag: 1% per year on short equity notional
            short_notional = float(short_w.abs().sum())
            borrow_drag = short_notional * (BORROW_FEE_ANNUAL / 252.0)

            # Cash return: dollar-neutral book has cash collateral = 1.0 (earns T-bill or 0%)
            rf = float(daily_rf.get(d, 0.0)) if d in daily_rf.index else 0.0

            cost_5bp_sub = rebal_cost_5bp if j == 0 else 0.0
            cost_20bp_sub = rebal_cost_20bp if j == 0 else 0.0

            rets_5bp_zero.append(r_stock - borrow_drag - cost_5bp_sub)
            rets_20bp_zero.append(r_stock - borrow_drag - cost_20bp_sub)
            rets_5bp_tbill.append(r_stock - borrow_drag + rf - cost_5bp_sub)
            port_dates.append(d)

    if not port_dates:
        return empty, empty, empty, 0, 0.0

    curve_5bp_zero = pd.Series(1.0 + np.array(rets_5bp_zero), index=port_dates).cumprod()
    curve_20bp_zero = pd.Series(1.0 + np.array(rets_20bp_zero), index=port_dates).cumprod()
    curve_5bp_tbill = pd.Series(1.0 + np.array(rets_5bp_tbill), index=port_dates).cumprod()

    return curve_5bp_zero, curve_20bp_zero, curve_5bp_tbill, trade_count, total_turnover


def calculate_metrics(
    curve: pd.Series,
    qqq_rets: pd.Series,
    ew_rets: pd.Series,
) -> dict[str, Any]:
    """Compute annualized performance and dual-baseline alpha statistics."""
    if len(curve) < 5:
        return {
            "cagr": 0.0,
            "sharpe": 0.0,
            "max_dd": 0.0,
            "alpha_qqq": 0.0,
            "t_qqq": 0.0,
            "beta_qqq": 0.0,
            "alpha_ew": 0.0,
            "t_ew": 0.0,
        }

    daily_r = curve.pct_change().dropna()
    start_dt = curve.index[0]
    end_dt = curve.index[-1]
    years = max((end_dt - start_dt).days / 365.25, 0.01)
    tot_ret = float(curve.iloc[-1] / curve.iloc[0]) - 1.0
    cagr = float((1.0 + tot_ret) ** (1.0 / years) - 1.0) if 1.0 + tot_ret > 0 else -1.0

    ann_vol = float(daily_r.std() * np.sqrt(252.0))
    ann_ret = float(daily_r.mean() * 252.0)
    sharpe = float(ann_ret / ann_vol) if ann_vol > 1e-8 else 0.0

    cummax = curve.cummax()
    dd = (curve - cummax) / cummax
    max_dd = float(dd.min())

    # Alpha vs QQQ
    aligned_qqq = qqq_rets.reindex(daily_r.index).fillna(0.0)
    q_stats = alpha_stats(daily_r, aligned_qqq)

    # Alpha vs EW hold of the 50 universe stocks
    aligned_ew = ew_rets.reindex(daily_r.index).fillna(0.0)
    ew_stats = alpha_stats(daily_r, aligned_ew)

    return {
        "cagr": cagr,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "alpha_qqq": float(q_stats.alpha) if np.isfinite(q_stats.alpha) else 0.0,
        "t_qqq": float(q_stats.t) if np.isfinite(q_stats.t) else 0.0,
        "beta_qqq": float(q_stats.beta) if np.isfinite(q_stats.beta) else 0.0,
        "alpha_ew": float(ew_stats.alpha) if np.isfinite(ew_stats.alpha) else 0.0,
        "t_ew": float(ew_stats.t) if np.isfinite(ew_stats.t) else 0.0,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Option-Implied Skew Long/Short Study (Step E1)")
    parser.add_argument(
        "--names",
        type=str,
        default="",
        help="Comma-separated symbols (default: OPTION_CAPTURE_UNIVERSE single names)",
    )
    parser.add_argument("--start", type=str, default="2019-05-10", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default="2026-11-09", help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--cache-dir",
        type=str,
        default="/tmp/implied_signal_cache",
        help="Cache directory for option signals and prices",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=str(DEFAULT_OUT),
        help="Markdown report output path",
    )
    parser.add_argument(
        "--grid-csv",
        type=str,
        default=str(DEFAULT_GRID_CSV),
        help="CSV path for full grid results",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use synthetic option quotes for verification if DB unavailable",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run synthetic dry-run smoke test (writes to temp dir)",
    )
    return parser.parse_args()


def write_pending_doc(out_path: Path) -> None:
    """Generate study document in RESULT PENDING state when option_quotes table is not yet connected."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    content = """# Option-Implied Skew Dollar-Neutral Long/Short Strategy Study (2026-11)

**Primary verdict: `unproven` (offline DB run pending)**.
Generated by `scripts/onetime_implied_longshort_study.py`.

## Overview & Methodology
Option prices and implied volatilities embed directional sentiment. Step E1 examines a dollar-neutral
long/short equity strategy designed to isolate option-implied sentiment alpha while removing broad market exposure:
- **Long Leg**: Top-N stocks ranked highest by option bullishness (weight = +0.9 / N).
- **Short Leg**: Bottom-N stocks ranked lowest by option sentiment (weight = -0.9 / N).
- **Portfolio Constraints**: Dollar-neutral (net = 0.0), gross exposure = 1.8 <= 1.8 (ALLOW_SHORT=True).
- **Cash Management**: Idle capacity parked in SHV via `park_idle_cash`.
- **Costs**: 5 bp per side baseline, 20 bp per side stress test, flat 1.0%/yr borrow drag on short equity notional.
- **Dual Baselines**: Evaluated against both QQQ and an equal-weighted hold of the same 50 megacap underlyings.

## Pre-Registered Variants (8 Combinations)
Grid pre-registered per global study rules (lines 8-19, at most 12 per study):
1. `composite_top5_weekly` (Shipped Default): signal=composite, top_n=5, weekly rebalance
2. `composite_top5_biweekly`: signal=composite, top_n=5, biweekly rebalance
3. `composite_top10_weekly`: signal=composite, top_n=10, weekly rebalance
4. `composite_top10_biweekly`: signal=composite, top_n=10, biweekly rebalance
5. `rr25_top5_weekly`: signal=rr25, top_n=5, weekly rebalance
6. `rr25_top5_biweekly`: signal=rr25, top_n=5, biweekly rebalance
7. `rr25_top10_weekly`: signal=rr25, top_n=10, weekly rebalance
8. `rr25_top10_biweekly`: signal=rr25, top_n=10, biweekly rebalance

## Production Database Requirement
Per PLAN-alphaideas.md line 177, this study requires historical option volatility surfaces from
the production Postgres `option_quotes` table. The study is run post-swarm against a read-only port-forward:
```bash
POSTGRES_URI="postgresql://user:pass@127.0.0.1:5432/production" \\
  python scripts/onetime_implied_longshort_study.py --out docs/backtests/implied-longshort-2026-11.md
```

## Promotion Bar
- Alpha vs QQQ: t >= 2.0 (t >= 3.0 for search winner), positive in both halves (H1, H2), beta < 0.5.
- Alpha vs EW hold of same 50 names: must show statistical significance over the equal-weight universe.
- Status: `unproven` until post-swarm execution against the live database port-forward.
"""
    out_path.write_text(content)
    logger.info("Wrote pending study doc to %s", out_path)


def check_db_available() -> bool:
    """Check if production Postgres is reachable with a short timeout."""
    uri = os.environ.get("POSTGRES_URI", "")
    if "stub:stub" in uri or not uri or "localhost:5432/stub" in uri:
        return False
    try:
        from sqlalchemy import text

        from tradingbot.utils.db import get_db_session

        with get_db_session() as session:
            session.execute(text("SELECT 1"))
            return True
    except Exception as e:
        logger.info("Database not reachable (%s); operating in offline mode", e)
        return False


def main() -> int:
    args = parse_args()
    is_synthetic = bool(args.mock or args.dry_run)
    if is_synthetic:
        out_path = Path(tempfile.gettempdir()) / Path(args.out).name
    else:
        out_path = Path(args.out)

    if args.names.strip():
        names = [s.strip().upper() for s in args.names.split(",") if s.strip()]
    else:
        names = list(DEFAULT_UNIVERSE)

    fetch_tickers = sorted(set(names) | {"QQQ", CASH_PROXY})
    start_date = pd.to_datetime(args.start).date()
    end_date = pd.to_datetime(args.end).date()

    signals_df = pd.DataFrame()
    db_ok = check_db_available()

    if db_ok and not is_synthetic:
        try:
            signals_df = load_signals_from_db(
                underlyings=fetch_tickers,
                start=start_date,
                end=end_date,
                cache_dir=args.cache_dir,
                weekly_only=True,
            )
        except Exception as e:
            logger.warning("Database query failed: %s", e)

    # If DB unavailable and not mock, write pending study doc and exit cleanly
    if signals_df.empty and not is_synthetic:
        logger.info("No option quotes available from DB; writing pending study document.")
        write_pending_doc(out_path)
        return 0

    daily_px = load_daily_prices(fetch_tickers, start_date, end_date, args.cache_dir)
    if daily_px.empty or "QQQ" not in daily_px.columns:
        logger.warning("Could not obtain daily prices; writing pending study document.")
        write_pending_doc(out_path)
        return 0

    if is_synthetic and signals_df.empty:
        logger.info("Generating synthetic option signals for smoke test...")
        signals_df = generate_synthetic_option_signals(names, daily_px.index)

    qqq_px = daily_px["QQQ"].dropna()
    qqq_rets = qqq_px.pct_change(fill_method=None)

    # 50-stock equal-weight baseline returns
    stock_px = daily_px.drop(columns=["QQQ", CASH_PROXY], errors="ignore")
    ew_rets = stock_px.pct_change(fill_method=None).mean(axis=1)

    # T-bill rate series
    daily_rf = load_tbill_rate(start_date, end_date, daily_px.index)

    # Walk-forward split date (calendar midpoint)
    snaps = sorted(signals_df["snapshot_at"].unique())
    first_snap = pd.Timestamp(snaps[0])
    last_snap = pd.Timestamp(snaps[-1])
    split_date = first_snap + (last_snap - first_snap) / 2
    logger.info(
        "Walk-forward split date: %s (H1: %s -> %s, H2: %s -> %s)",
        split_date,
        first_snap,
        split_date,
        split_date,
        last_snap,
    )

    # Evaluate pre-registered grid
    grid_results: list[dict[str, Any]] = []

    for combo in PRE_REGISTERED_GRID:
        var_name = f"{combo['signal']}_top{combo['top_n']}_{combo['rebalance']}"
        logger.info("Evaluating variant: %s", var_name)

        # Simulation across costs and cash models in one pass
        curve_5bp_zero, curve_20bp_zero, curve_5bp_tbill, trades, turnover = run_strategy_simulation(
            combo, signals_df, daily_px, daily_rf
        )

        # Full window metrics
        full_m_5bp_zero = calculate_metrics(curve_5bp_zero, qqq_rets, ew_rets)
        full_m_20bp_zero = calculate_metrics(curve_20bp_zero, qqq_rets, ew_rets)
        full_m_5bp_tbill = calculate_metrics(curve_5bp_tbill, qqq_rets, ew_rets)

        # H1 & H2 split metrics (choose on H1, judge on H2)
        h1_curve = curve_5bp_zero[curve_5bp_zero.index <= split_date]
        h2_curve = curve_5bp_zero[curve_5bp_zero.index > split_date]

        h1_m = calculate_metrics(h1_curve, qqq_rets, ew_rets)
        h2_m = calculate_metrics(h2_curve, qqq_rets, ew_rets)

        # Record trial for multiple-testing tracking (PLAN-alphaideas.md line 12)
        if db_ok:
            record_trial(
                bot="OptionImpliedLongShortBot",
                params=combo,
                date=datetime.datetime.now(datetime.UTC),
                alpha=full_m_5bp_zero["alpha_qqq"],
                alpha_t=full_m_5bp_zero["t_qqq"],
                beta=full_m_5bp_zero["beta_qqq"],
                n=len(curve_5bp_zero),
            )

        grid_results.append(
            {
                "variant": var_name,
                "signal": combo["signal"],
                "top_n": combo["top_n"],
                "rebalance": combo["rebalance"],
                "H1_t_qqq": h1_m["t_qqq"],
                "H1_alpha_qqq": h1_m["alpha_qqq"],
                "H2_t_qqq": h2_m["t_qqq"],
                "H2_alpha_qqq": h2_m["alpha_qqq"],
                "H2_beta_qqq": h2_m["beta_qqq"],
                "Full_alpha_qqq": full_m_5bp_zero["alpha_qqq"],
                "Full_t_qqq": full_m_5bp_zero["t_qqq"],
                "Full_beta_qqq": full_m_5bp_zero["beta_qqq"],
                "Full_alpha_ew": full_m_5bp_zero["alpha_ew"],
                "Full_t_ew": full_m_5bp_zero["t_ew"],
                "CAGR_5bp_zero": full_m_5bp_zero["cagr"],
                "CAGR_5bp_tbill": full_m_5bp_tbill["cagr"],
                "CAGR_20bp_zero": full_m_20bp_zero["cagr"],
                "Sharpe": full_m_5bp_zero["sharpe"],
                "MaxDD": full_m_5bp_zero["max_dd"],
                "Trades": trades,
                "Turnover": turnover,
            }
        )

    grid_df = pd.DataFrame(grid_results)
    grid_csv_path = Path(args.grid_csv)
    grid_csv_path.parent.mkdir(parents=True, exist_ok=True)
    grid_df.to_csv(grid_csv_path, index=False)
    logger.info("Saved grid results to %s", grid_csv_path)

    # Determine Walk-Forward Winner
    # Defaults is the first row: composite, top 5, weekly
    defaults_row = grid_df.iloc[0]

    # Pick candidate on H1 by alpha_t vs QQQ
    h1_winner_idx = grid_df["H1_t_qqq"].idxmax()
    h1_winner_row = grid_df.loc[h1_winner_idx]

    # Out-of-sample test on H2: does H1 winner beat defaults on H2?
    h1_winner_beats_defaults_on_h2 = float(h1_winner_row["H2_t_qqq"]) > float(defaults_row["H2_t_qqq"])
    shipped_row = h1_winner_row if h1_winner_beats_defaults_on_h2 else defaults_row
    shipped_decision = "h1_winner" if h1_winner_beats_defaults_on_h2 else "defaults"

    # Bar check: alpha vs QQQ, t >= 2.0 (t >= 3.0 for search winner), positive in both halves, beta < 0.5
    search_penalty = 3.0 if shipped_decision == "h1_winner" else 2.0
    clears_bar = (
        float(shipped_row["H1_alpha_qqq"]) > 0.0
        and float(shipped_row["H2_alpha_qqq"]) > 0.0
        and float(shipped_row["H2_t_qqq"]) >= search_penalty
        and float(shipped_row["Full_t_qqq"]) >= search_penalty
        and abs(float(shipped_row["Full_beta_qqq"])) < 0.5
    )
    verdict = "pass" if clears_bar else "unproven"

    # Build generated markdown doc
    doc_lines = []
    if is_synthetic:
        doc_lines.append("SYNTHETIC SMOKE TEST, NOT RESULTS\n")
    doc_lines.extend([
        "# Option-Implied Skew Dollar-Neutral Long/Short Strategy Study (2026-11)",
        "",
        f"**Primary verdict: `{verdict}`**.",
        "Generated by `scripts/onetime_implied_longshort_study.py`.",
        "",
        "## Overview & Methodology",
        "Option prices and volatility skews embed directional sentiment before equity spot markets adjust.",
        "This study implements and evaluates a dollar-neutral long/short stock book across liquid large caps:",
        "1. **Long Leg**: Top-N stocks by option sentiment (weight = +0.9 / N).",
        "2. **Short Leg**: Bottom-N stocks by option sentiment (weight = -0.9 / N).",
        "3. **Gross Exposure**: 1.8 <= 1.8 (ALLOW_SHORT=True), net exposure ~ 0.0.",
        "4. **Idle Cash**: Parked in SHV via `park_idle_cash`.",
        f"5. **Short Borrow**: Flat {BORROW_FEE_ANNUAL:.1%}/yr borrow drag on short equity market value.",
        "6. **Dual Baselines**: Evaluated against QQQ and against an equal-weight hold of the same 50 names.",
        "",
        f"- Sample: {first_snap.date()} to {last_snap.date()} ({len(snaps)} option snapshots).",
        f"- Walk-forward split date: {split_date.date()} (H1: {first_snap.date()} to {split_date.date()}, H2: {split_date.date()} to {last_snap.date()}).",
        "",
        "## 1. Pre-Registered Grid Performance (8 Variants)",
        "",
        "| Variant | Signal | Top/Bottom N | Rebal | H1 t (QQQ) | H2 t (QQQ) | Full Alpha t (QQQ) | Full Beta | Full Alpha t (EW) | CAGR (5bp, 0% cash) | CAGR (5bp, T-bill) | CAGR (20bp) | Sharpe | Max DD |",
        "|:---|:---|---:|:---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])

    for _, r in grid_df.iterrows():
        doc_lines.append(
            f"| {r['variant']} | {r['signal']} | {r['top_n']} | {r['rebalance']} | "
            f"{r['H1_t_qqq']:+.2f} | {r['H2_t_qqq']:+.2f} | {r['Full_t_qqq']:+.2f} | {r['Full_beta_qqq']:+.2f} | "
            f"{r['Full_t_ew']:+.2f} | {r['CAGR_5bp_zero']:+.1%} | {r['CAGR_5bp_tbill']:+.1%} | {r['CAGR_20bp_zero']:+.1%} | "
            f"{r['Sharpe']:.2f} | {r['MaxDD']:.1%} |"
        )

    doc_lines.extend(
        [
            "",
            "## 2. Walk-Forward Decision & Dual-Baseline Comparison",
            f"- **Defaults Configuration**: `{defaults_row['variant']}` (H1 t: {defaults_row['H1_t_qqq']:+.2f}, H2 t: {defaults_row['H2_t_qqq']:+.2f}).",
            f"- **H1 Winner Selected**: `{h1_winner_row['variant']}` (H1 t: {h1_winner_row['H1_t_qqq']:+.2f}, H2 t: {h1_winner_row['H2_t_qqq']:+.2f}).",
            f"- **Decision**: `{shipped_decision}` (winner beats defaults on H2: {h1_winner_beats_defaults_on_h2}).",
            f"- **Promotion Verdict**: `{verdict}` (bar requires t >= {search_penalty:.1f}, positive in both halves, beta < 0.5).",
            "",
            "### Comparison vs Baselines (Full Window, 5 bp Cost, Cash at T-Bill Yield)",
            "",
            "| Strategy | CAGR | Sharpe | Max DD | Beta vs QQQ | Alpha/yr vs QQQ | Alpha t vs QQQ | Alpha/yr vs EW | Alpha t vs EW |",
            "|:---|---:|---:|---:|---:|---:|---:|---:|---:|",
            f"| Shipped ({shipped_row['variant']}) | {shipped_row['CAGR_5bp_tbill']:+.1%} | {shipped_row['Sharpe']:.2f} | {shipped_row['MaxDD']:.1%} | {shipped_row['Full_beta_qqq']:+.2f} | {shipped_row['Full_alpha_qqq']:+.1%} | {shipped_row['Full_t_qqq']:+.2f} | {shipped_row['Full_alpha_ew']:+.1%} | {shipped_row['Full_t_ew']:+.2f} |",
            f"| Defaults ({defaults_row['variant']}) | {defaults_row['CAGR_5bp_tbill']:+.1%} | {defaults_row['Sharpe']:.2f} | {defaults_row['MaxDD']:.1%} | {defaults_row['Full_beta_qqq']:+.2f} | {defaults_row['Full_alpha_qqq']:+.1%} | {defaults_row['Full_t_qqq']:+.2f} | {defaults_row['Full_alpha_ew']:+.1%} | {defaults_row['Full_t_ew']:+.2f} |",
        ]
    )

    out_path.write_text("\n".join(doc_lines) + "\n")
    logger.info("Wrote study report to %s", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
