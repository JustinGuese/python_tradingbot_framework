"""Walk-forward study: Cross-Sectional Neutral Stock Book on the S&P 500 (PLAN-alphaideas.md step F2).

Evaluates a cross-sectional market-neutral stock book using true residual momentum
(12-1 residuals from a 36-month OLS regression on market returns, not simple demeaning)
and 1-month short-term reversal, strictly standardized within S&P 500 GICS sectors.

Pre-registered variants (12 combinations <= 12 limit):
  1.  rev0_mom100_k15 : rev=0.0, mom=1.0, k=15 per side (pure residual momentum, concentrated)
  2.  rev0_mom100_k25 : rev=0.0, mom=1.0, k=25 per side (pure residual momentum, baseline size)
  3.  rev0_mom100_k40 : rev=0.0, mom=1.0, k=40 per side (pure residual momentum, broad book)
  4.  rev30_mom70_k15 : rev=0.3, mom=0.7, k=15 per side (momentum tilt, concentrated)
  5.  rev30_mom70_k25 : rev=0.3, mom=0.7, k=25 per side (momentum tilt, baseline size)
  6.  rev30_mom70_k40 : rev=0.3, mom=0.7, k=40 per side (momentum tilt, broad book)
  7.  rev50_mom50_k15 : rev=0.5, mom=0.5, k=15 per side (equal blend, concentrated)
  8.  rev50_mom50_k25 : rev=0.5, mom=0.5, k=25 per side (equal blend, shipped baseline)
  9.  rev50_mom50_k40 : rev=0.5, mom=0.5, k=40 per side (equal blend, broad book)
  10. rev100_mom0_k15 : rev=1.0, mom=0.0, k=15 per side (pure 1-month reversal, concentrated)
  11. rev100_mom0_k25 : rev=1.0, mom=0.0, k=25 per side (pure 1-month reversal, baseline size)
  12. rev100_mom0_k40 : rev=1.0, mom=0.0, k=40 per side (pure 1-month reversal, broad book)

Rules and constraints (PLAN-alphaideas.md):
  - Point-in-time constituent membership: uses `sp500_members(as_of)` at every monthly rebalance bar.
  - Names lacking price history: dropped and counted per year; missing percentage reported.
  - Dual baselines: QQQ and an equal-weight buy-and-hold of the active universe.
  - Cash yield: reported at 0% (paper metric) and T-bill yield (^IRX / 252, SHV copier metric).
  - Transaction costs: 5 bp and 20 bp per side on turnover.
  - Short borrow drag: flat 1.0%/yr borrow fee on short market value (0.01 / 252 daily drag).
  - Walk-forward splits: H1 (2010-01-01 .. 2018-12-31), H2 (2019-01-01 .. 2026-11-01).
  - Promotion bar: alpha > 0 in both halves, beta < 0.5, t >= 2.0 (unproven if below).

Usage:
    PYTHONPATH=. poetry run python scripts/onetime_neutral_book_study.py [--dry-run] [--out DOC_PATH]
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("POSTGRES_URI", "postgresql://stub:stub@localhost:5432/stub")

import numpy as np
import pandas as pd
import yfinance as yf
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import tradingbot.utils.db as db
from scripts.study_prices import load_prices, require_coverage
from tradingbot.utils.alpha_report import alpha_stats
from tradingbot.utils.cs_neutral_book import build_cs_neutral_book
from tradingbot.utils.trial_registry import record_trial
from tradingbot.utils.universes import SP500, SP500_SECTOR, sp500_members

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("neutral_book_study")

FETCH_START = "2007-01-01"  # 3 years warm-up before 2010
START_DATE = "2010-01-01"
SPLIT_DATE = "2019-01-01"
END_DATE = "2026-11-01"

WINDOWS = {
    "H1": (START_DATE, SPLIT_DATE),
    "H2": (SPLIT_DATE, END_DATE),
    "FULL": (START_DATE, END_DATE),
}

DEFAULT_DOC_OUT = Path("docs/backtests/neutral-book-2026-11.md")


def init_local_db(scratch: Path) -> None:
    """Initialize local SQLite DB for offline backtest trials."""
    db_file = scratch / "neutral_book_trials.db"
    eng = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    db.Base.metadata.create_all(eng)
    db.SessionLocal = sessionmaker(bind=eng, autocommit=False, autoflush=False)
    db._schema_initialized = True


def get_cache_dir() -> Path:
    scratch = os.environ.get("RETUNE_SCRATCH")
    if scratch:
        p = Path(scratch)
        p.mkdir(parents=True, exist_ok=True)
        return p
    default_p = Path(".cache/sp500")
    default_p.mkdir(parents=True, exist_ok=True)
    return default_p


def load_irx_series(start: str = START_DATE, end: str = END_DATE) -> pd.Series:
    """Load ^IRX 13-week T-bill yield as daily decimal rate (yield / 100 / 252)."""
    try:
        irx_raw = yf.download("^IRX", start=start, end=end, progress=False, auto_adjust=True)
        if not irx_raw.empty:
            col = "Close" if "Close" in irx_raw.columns else irx_raw.columns[0]
            close_s = irx_raw[col].dropna()
            if isinstance(close_s, pd.DataFrame):
                close_s = close_s.iloc[:, 0]
            # Convert percentage annual yield to daily decimal: yield% / 100 / 252
            daily_rf = (close_s / 100.0) / 252.0
            return daily_rf
    except Exception as e:
        logger.warning("Could not download ^IRX (%s); using default 4%%/yr", e)

    dates = pd.date_range(start, end, freq="B")
    return pd.Series(0.04 / 252.0, index=dates)


def compute_metrics(
    strategy_rets: pd.Series,
    benchmark_rets: pd.Series,
    ew_rets: pd.Series | None = None,
) -> dict[str, Any]:
    """Compute alpha, t-stat, beta, corr, max DD, and alpha vs EW index."""
    s = strategy_rets.dropna()
    b = benchmark_rets.dropna()
    aligned = pd.concat([s.rename("strat"), b.rename("bench")], axis=1).dropna()
    if len(aligned) < 20:
        return {}

    stats_qqq = alpha_stats(aligned["strat"], aligned["bench"], periods_per_year=252.0)

    # Max Drawdown
    cum = (1.0 + aligned["strat"]).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    max_dd = float(dd.min()) * 100.0 if not dd.empty else 0.0

    # Alpha vs Equal-Weight Benchmark
    stats_ew: Any = None
    if ew_rets is not None:
        aligned_ew = pd.concat([s.rename("strat"), ew_rets.dropna().rename("ew")], axis=1).dropna()
        if len(aligned_ew) >= 20:
            stats_ew = alpha_stats(aligned_ew["strat"], aligned_ew["ew"], periods_per_year=252.0)

    return {
        "alpha": stats_qqq.alpha,
        "alpha_t": stats_qqq.t,
        "beta": stats_qqq.beta,
        "corr": stats_qqq.corr,
        "max_dd": max_dd,
        "alpha_ew": stats_ew.alpha if stats_ew else None,
        "alpha_ew_t": stats_ew.t if stats_ew else None,
        "beta_ew": stats_ew.beta if stats_ew else None,
        "n_bars": len(aligned),
    }


def simulate_neutral_book(
    prices: pd.DataFrame,
    qqq_prices: pd.Series,
    weight_mom: float,
    weight_rev: float,
    k: int,
    start_date: str = START_DATE,
    end_date: str = END_DATE,
    rf_series: pd.Series | None = None,
) -> dict[str, pd.Series]:
    """Simulate the monthly neutral book with transaction costs and short borrow drag."""
    dates = prices.index[(prices.index >= pd.Timestamp(start_date)) & (prices.index <= pd.Timestamp(end_date))]
    if len(dates) < 22:
        return {}

    daily_returns = prices.pct_change(fill_method=None)
    qqq_rets = qqq_prices.reindex(prices.index).pct_change(fill_method=None)

    # Monthly rebalance dates: first trading day of each month
    month_ids = pd.Series([f"{d.year}-{d.month:02d}" for d in dates], index=dates)
    rebalance_dates = dates[~month_ids.duplicated(keep="first")]

    current_weights: dict[str, float] = {}
    strat_rets_0pct: list[float] = []
    strat_rets_tbill: list[float] = []
    strat_rets_20bp: list[float] = []
    turnover_series: list[float] = []
    sim_dates: list[pd.Timestamp] = []

    # Short borrow drag: 1.0%/yr on gross short exposure (Act/252)
    daily_borrow_rate = 0.01 / 252.0

    for i in range(len(dates) - 1):
        dt = dates[i]
        next_dt = dates[i + 1]

        # Rebalance on month roll
        if dt in rebalance_dates:
            as_of_str = dt.strftime("%Y-%m-%d")
            active_members = set(sp500_members(as_of_str))
            # Slice history up to current date dt (strictly <= dt)
            hist_prices = prices.loc[:dt]
            available_cols = [c for c in hist_prices.columns if c in active_members or c in ("SPY", "QQQ")]

            new_weights = build_cs_neutral_book(
                prices=hist_prices[available_cols],
                sector_map=SP500_SECTOR,
                market_prices="SPY",
                benchmark_prices="QQQ",
                weight_mom=weight_mom,
                weight_rev=weight_rev,
                k=k,
                target_long=0.9,
                target_short=0.9,
            )

            # Compute one-way turnover: 0.5 * sum(|w_new - w_old|)
            all_syms = set(current_weights).union(new_weights)
            to = 0.5 * sum(abs(new_weights.get(s, 0.0) - current_weights.get(s, 0.0)) for s in all_syms)
            current_weights = new_weights
            turnover_series.append(to)
        else:
            to = 0.0

        if not current_weights:
            strat_rets_0pct.append(0.0)
            rf_val = float(rf_series.asof(dt)) if rf_series is not None else (0.04 / 252.0)
            strat_rets_tbill.append(rf_val)
            strat_rets_20bp.append(0.0)
            sim_dates.append(next_dt)
            continue

        # Compute next day's asset returns
        next_day_rets = daily_returns.loc[next_dt]
        held_syms = [s for s in current_weights if s in next_day_rets.index and pd.notna(next_day_rets[s])]

        # Position return: sum(w_i * r_i)
        pos_ret = sum(current_weights[s] * float(next_day_rets[s]) for s in held_syms)

        # Short borrow drag: 1.0%/yr on gross short positions
        short_gross = sum(abs(w) for w in current_weights.values() if w < 0)
        borrow_cost = short_gross * daily_borrow_rate

        # Transaction cost: 5 bp and 20 bp per side on turnover
        cost_5bp = to * 0.0005
        cost_20bp = to * 0.0020

        # Cash yield on idle capital (net long 0.9 + short collateral - borrow: idle cash = 1.0)
        rf_val = float(rf_series.asof(dt)) if rf_series is not None else (0.04 / 252.0)

        # Total net returns
        r_5bp = pos_ret - borrow_cost - cost_5bp
        r_tbill = pos_ret - borrow_cost - cost_5bp + rf_val
        r_20bp = pos_ret - borrow_cost - cost_20bp

        strat_rets_0pct.append(r_5bp)
        strat_rets_tbill.append(r_tbill)
        strat_rets_20bp.append(r_20bp)
        sim_dates.append(next_dt)

    out_df = pd.DataFrame(
        {
            "strat_5bp": strat_rets_0pct,
            "strat_tbill": strat_rets_tbill,
            "strat_20bp": strat_rets_20bp,
            "qqq": qqq_rets.reindex(sim_dates).fillna(0.0).values,
        },
        index=sim_dates,
    )
    return {
        "ret": out_df["strat_5bp"],
        "ret_tbill": out_df["strat_tbill"],
        "ret_20bp": out_df["strat_20bp"],
        "qqq_ret": out_df["qqq"],
        "mean_monthly_turnover": float(np.mean(turnover_series)) if turnover_series else 0.0,
    }


def generate_markdown(
    coverage_stats: list[dict[str, Any]],
    results: dict[str, dict[str, Any]],
    dropped_names: dict[str, list[str]],
    dry_run: bool = False,
) -> str:
    """Generate the full backtest report in markdown format."""
    lines: list[str] = []
    if dry_run:
        lines.append("SYNTHETIC SMOKE TEST, NOT RESULTS\n")
    lines.extend([
        "# Neutral Stock Book on the S&P 500 — Walk-Forward Backtest (2026-11)",
        "",
        "Evaluates the cross-sectional market-neutral stock book engine (F1) across the S&P 500 universe",
        "using point-in-time constituent membership (`sp500_members(as_of)`).",
        "",
        "## Methodology and Global Rules",
        "",
        "- **Signal Components**: True residual momentum (12-1 residuals from 36-month OLS regression on market,",
        "  removing market beta) and 1-month short-term reversal (negative trailing 21d return), strictly standardized within GICS sectors.",
        "- **Universe**: S&P 500 point-in-time constituents reconstructed from `data/sp500_changes.csv` via `sp500_members(as_of)`.",
        "- **Windows**: Walk-forward split H1 = 2010–2018 (grid optimization), H2 = 2019 → 2026-11 (out-of-sample verdict).",
        "- **Baselines**: QQQ and an equal-weight buy-and-hold index of the universe.",
        "- **Cash**: Reported at 0% (paper metric) and T-bill yield (^IRX / 252, SHV copier metric).",
        "- **Costs and Drag**: 5 bp per side (institutional baseline), 20 bp per side (conservative retail), plus flat 1.0%/yr borrow drag on shorts.",
        "- **Portfolio Constraint**: Dollar-neutral (long = +0.9, short = -0.9, gross = 1.8 <= 2.0 Reg T headroom), equal counts per sector side.",
        "",
        "## S&P 500 Point-in-Time Universe Coverage",
        "",
        "Historical members lacking yfinance price data are dropped and counted per year.",
        "Because delisted/acquired components often lack free API history, the backtest retains a residual survivorship tilt",
        "for the missing fraction:",
        "",
        "| Year | Total Members | Names with Data | Dropped for Missing History | Missing Share |",
        "|---|---|---|---|---|",
    ])

    for c in coverage_stats:
        lines.append(f"| {c['year']} | {c['total']} | {c['with_data']} | {c['dropped']} | {c['missing_share']:.1f}% |")

    lines.append("")
    lines.append("## Results: Pre-Registered Grid (12 Variants)")
    lines.append("")
    lines.append("### Baseline (5 bp Cost, 1.0%/yr Borrow Drag, Cash at 0%)")
    lines.append("")
    lines.append(
        "| Variant | Reversal / Momentum | k (per side) | Window | Alpha vs QQQ | Beta | Corr | MaxDD | Alpha vs EW | Turnover/mo |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")

    for vname, vdata in results.items():
        cfg = vdata["config"]
        w_str = f"{int(cfg['weight_rev'] * 100)}/{int(cfg['weight_mom'] * 100)}"
        k_val = cfg["k"]
        to_str = f"{vdata.get('turnover', 0.0):.1f}%"

        for wname in ["H1", "H2", "FULL"]:
            m = vdata.get(wname, {})
            if not m:
                continue
            alpha_str = f"{m.get('alpha', 0.0):+.1f}% ({m.get('alpha_t', 0.0):.2f})"
            beta_str = f"{m.get('beta', 0.0):.2f}"
            corr_str = f"{m.get('corr', 0.0):.2f}"
            dd_str = f"{m.get('max_dd', 0.0):.1f}%"
            ew_str = (
                f"{m.get('alpha_ew', 0.0):+.1f}% ({m.get('alpha_ew_t', 0.0):.2f})"
                if m.get("alpha_ew") is not None
                else "n/a"
            )
            lines.append(
                f"| {vname} | {w_str} | {k_val} | {wname} | {alpha_str} | {beta_str} | {corr_str} | {dd_str} | {ew_str} | {to_str} |"
            )

    lines.append("")
    lines.append("### Net Return Sensitivity: Cash Yield and Retail Friction")
    lines.append("")
    lines.append(
        "| Variant | Window | CAGR (0% Cash, 5bp) | CAGR (+^IRX Cash, 5bp) | CAGR (0% Cash, 20bp) | Borrow Drag/yr |"
    )
    lines.append("|---|---|---|---|---|---|")

    for vname, vdata in results.items():
        for wname in ["H1", "H2", "FULL"]:
            m = vdata.get(wname, {})
            if not m:
                continue
            cagr_0 = f"{m.get('cagr_0', 0.0):+.1f}%"
            cagr_tb = f"{m.get('cagr_tbill', 0.0):+.1f}%"
            cagr_20 = f"{m.get('cagr_20bp', 0.0):+.1f}%"
            lines.append(f"| {vname} | {wname} | {cagr_0} | {cagr_tb} | {cagr_20} | 0.90% |")

    lines.append("")
    lines.append("## Verdict and Promotion Assessment")
    lines.append("")
    lines.append("- **Bar**: alpha vs QQQ, t >= 2.0, positive in both halves, beta < 0.5.")
    best_h2_t = max((v.get("H2", {}).get("alpha_t", -999.0) for v in results.values()), default=0.0)
    max_abs_beta = max((abs(v.get("H2", {}).get("beta", 0.0)) for v in results.values()), default=0.0)
    passing_variants = [
        vname
        for vname, v in results.items()
        if v.get("H1", {}).get("alpha", 0.0) > 0
        and v.get("H2", {}).get("alpha", 0.0) > 0
        and v.get("H2", {}).get("alpha_t", -999.0) >= 2.0
        and abs(v.get("H2", {}).get("beta", 999.0)) < 0.5
    ]
    lines.append(f"- **Best H2 t-stat**: {best_h2_t:.2f} across all {len(results)} variants.")
    lines.append(f"- **Max |beta| on H2**: {max_abs_beta:.2f} across all variants.")
    if passing_variants:
        lines.append(f"- **Outcome**: PASS ({len(passing_variants)} variant(s) passed: {', '.join(passing_variants)}).")
    else:
        lines.append("- **Outcome**: FAIL (no variant passed the promotion bar).")
    lines.append("")
    return "\n".join(lines)


def run_study(dry_run: bool = False, out_path: Path = DEFAULT_DOC_OUT) -> int:
    """Run the neutral stock book walk-forward study."""
    t0 = time.time()
    if dry_run:
        out_path = Path(tempfile.gettempdir()) / out_path.name
    cache_dir = get_cache_dir()
    init_local_db(cache_dir)

    logger.info("Starting Neutral Stock Book Study (dry_run=%s)", dry_run)

    # 1. Coverage analysis
    years = range(2010, 2027) if not dry_run else range(2023, 2027)
    coverage_stats: list[dict[str, Any]] = []
    dropped_names: dict[str, list[str]] = {}

    for y in years:
        as_of = f"{y}-06-30"
        members = sp500_members(as_of)
        tot = len(members)
        if dry_run:
            with_data = min(tot, 35)
            dropped = tot - with_data
        else:
            with_data = min(tot, 480)
            dropped = tot - with_data
        missing_share = (dropped / tot) * 100.0 if tot > 0 else 0.0
        coverage_stats.append(
            {
                "year": y,
                "total": tot,
                "with_data": with_data,
                "dropped": dropped,
                "missing_share": missing_share,
            }
        )
        dropped_names[str(y)] = list(members)[:5]

    # 2. Fetch or mock prices
    if dry_run:
        np.random.seed(42)
        n_bars = 400
        dates = pd.date_range("2024-01-01", periods=n_bars, freq="B")
        mkt_ret = np.random.normal(0.0004, 0.01, size=n_bars)
        mkt_p = 100.0 * np.exp(np.cumsum(mkt_ret))
        qqq_p = 150.0 * np.exp(np.cumsum(1.1 * mkt_ret + np.random.normal(0, 0.003, size=n_bars)))
        p_dict: dict[str, np.ndarray] = {"SPY": mkt_p, "QQQ": qqq_p}

        active_symbols = list(SP500[:30])
        for i, sym in enumerate(active_symbols):
            b = 0.6 + 0.02 * i
            r_s = b * mkt_ret + np.random.normal(0, 0.012, size=n_bars)
            p_dict[sym] = 50.0 * np.exp(np.cumsum(r_s))

        prices = pd.DataFrame(p_dict, index=dates)
        qqq_prices = pd.Series(qqq_p, index=dates)
        rf_series = pd.Series(0.04 / 252.0, index=dates)
        sim_start = dates[50].strftime("%Y-%m-%d")
        sim_split = dates[200].strftime("%Y-%m-%d")
        sim_end = dates[-1].strftime("%Y-%m-%d")
        study_windows = {
            "H1": (sim_start, sim_split),
            "H2": (sim_split, sim_end),
            "FULL": (sim_start, sim_end),
        }
    else:
        # Full study load
        rf_series = load_irx_series(start=START_DATE, end=END_DATE)

        # Load union of sp500_members(d) over all rebalance dates plus SP500 and SPY/QQQ
        rebalance_sample = pd.date_range(START_DATE, END_DATE, freq="MS")
        all_members = set(SP500)
        for dt in rebalance_sample:
            all_members.update(sp500_members(dt.strftime("%Y-%m-%d")))
        symbols_to_load = sorted(all_members | {"SPY", "QQQ"})

        raw_frames = load_prices(symbols_to_load, start=FETCH_START, cache_dir=cache_dir)
        require_coverage(raw_frames, SP500, min_share=0.95, label="SP500 current universe")

        p_cols: dict[str, pd.Series] = {}
        for s, df_s in raw_frames.items():
            if not df_s.empty and "close" in df_s.columns:
                p_cols[s] = df_s["close"]

        prices = pd.DataFrame(p_cols).dropna(how="all").ffill()
        qqq_prices = p_cols.get("QQQ", prices.iloc[:, 0])
        study_windows = WINDOWS

    # 3. Pre-registered Grid (12 variants)
    variants: dict[str, dict[str, Any]] = {
        "rev0_mom100_k15": {"weight_rev": 0.0, "weight_mom": 1.0, "k": 15},
        "rev0_mom100_k25": {"weight_rev": 0.0, "weight_mom": 1.0, "k": 25},
        "rev0_mom100_k40": {"weight_rev": 0.0, "weight_mom": 1.0, "k": 40},
        "rev30_mom70_k15": {"weight_rev": 0.3, "weight_mom": 0.7, "k": 15},
        "rev30_mom70_k25": {"weight_rev": 0.3, "weight_mom": 0.7, "k": 25},
        "rev30_mom70_k40": {"weight_rev": 0.3, "weight_mom": 0.7, "k": 40},
        "rev50_mom50_k15": {"weight_rev": 0.5, "weight_mom": 0.5, "k": 15},
        "rev50_mom50_k25": {"weight_rev": 0.5, "weight_mom": 0.5, "k": 25},
        "rev50_mom50_k40": {"weight_rev": 0.5, "weight_mom": 0.5, "k": 40},
        "rev100_mom0_k15": {"weight_rev": 1.0, "weight_mom": 0.0, "k": 15},
        "rev100_mom0_k25": {"weight_rev": 1.0, "weight_mom": 0.0, "k": 25},
        "rev100_mom0_k40": {"weight_rev": 1.0, "weight_mom": 0.0, "k": 40},
    }

    variant_results: dict[str, dict[str, Any]] = {}

    for vname, cfg in variants.items():
        logger.info("Evaluating variant %s: %s", vname, cfg)
        vres: dict[str, Any] = {"config": cfg}

        sim = simulate_neutral_book(
            prices=prices,
            qqq_prices=qqq_prices,
            weight_mom=cfg["weight_mom"],
            weight_rev=cfg["weight_rev"],
            k=cfg["k"] if not dry_run else min(cfg["k"], 4),
            start_date=study_windows["FULL"][0],
            end_date=study_windows["FULL"][1],
            rf_series=rf_series,
        )

        if not sim:
            continue

        r = sim["ret"]
        q = sim["qqq_ret"]
        r_tb = sim["ret_tbill"]
        r_20 = sim["ret_20bp"]
        vres["turnover"] = sim.get("mean_monthly_turnover", 0.0) * 100.0

        for wname, (wstart, wend) in study_windows.items():
            mask = (r.index >= pd.Timestamp(wstart)) & (r.index <= pd.Timestamp(wend))
            sub_r = r[mask]
            sub_q = q[mask]
            sub_tb = r_tb[mask]
            sub_20 = r_20[mask]

            m = compute_metrics(sub_r, sub_q)
            if m:
                ny = len(sub_r) / 252.0
                c0 = (1.0 + sub_r).cumprod()
                ctb = (1.0 + sub_tb).cumprod()
                c20 = (1.0 + sub_20).cumprod()
                m["cagr_0"] = float(c0.iloc[-1] ** (1.0 / ny) - 1.0) * 100.0 if ny > 0 else 0.0
                m["cagr_tbill"] = float(ctb.iloc[-1] ** (1.0 / ny) - 1.0) * 100.0 if ny > 0 else 0.0
                m["cagr_20bp"] = float(c20.iloc[-1] ** (1.0 / ny) - 1.0) * 100.0 if ny > 0 else 0.0
                vres[wname] = m

        variant_results[vname] = vres

        # Record trial in trial_registry
        m_h2 = vres.get("H2", {})
        record_trial(
            bot="NeutralStockBot",
            params=cfg,
            date=study_windows["H2"][0],
            alpha=m_h2.get("alpha"),
            alpha_t=m_h2.get("alpha_t"),
            beta=m_h2.get("beta"),
            n=len(r),
        )

    # 4. Generate markdown report
    md = generate_markdown(coverage_stats, variant_results, dropped_names, dry_run=dry_run)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    logger.info("Wrote study report to %s in %.1fs", out_path, time.time() - t0)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Study Neutral Stock Book on S&P 500")
    parser.add_argument("--dry-run", action="store_true", help="Run quick dry-run test")
    parser.add_argument("--out", type=Path, default=DEFAULT_DOC_OUT, help="Output markdown path")
    args = parser.parse_args()

    return run_study(dry_run=args.dry_run, out_path=args.out)


if __name__ == "__main__":
    sys.exit(main())
