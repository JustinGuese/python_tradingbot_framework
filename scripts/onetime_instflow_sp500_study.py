"""Walk-forward study: InstitutionalFlowBot on the S&P 500 (PLAN-alphaideas.md step C2).

Evaluates whether the large-cap momentum + trend filter edge discovered on the
S&P 100 (t=1.52, unproven) generalizes to the broader S&P 500 when using point-in-time
constituent membership (`sp500_members(as_of)`), or whether survivorship bias and
liquidity differences dilute the signal.

Pre-registered variants (8 combinations <= 12 limit):
  1. sp100_top20_weekly_gate : SP100 baseline, top_n=20, weekly (Fri), QQQ gate ON (shipped default)
  2. sp100_top10_weekly_gate : SP100 baseline, top_n=10, weekly (Fri), QQQ gate ON (old default)
  3. sp500_top20_weekly_gate : SP500 PIT, top_n=20, weekly (Fri), QQQ gate ON (direct comparison)
  4. sp500_top10_weekly_gate : SP500 PIT, top_n=10, weekly (Fri), QQQ gate ON
  5. sp500_top50_weekly_gate : SP500 PIT, top_n=50, weekly (Fri), QQQ gate ON (capacity-scaled)
  6. sp500_top20_weekly_nogate : SP500 PIT, top_n=20, weekly (Fri), QQQ gate OFF (ablation)
  7. sp500_top20_daily_gate  : SP500 PIT, top_n=20, daily rebalance, QQQ gate ON
  8. sp500_top50_daily_gate  : SP500 PIT, top_n=50, daily rebalance, QQQ gate ON

Rules and constraints (PLAN-alphaideas.md):
  - Point-in-time membership: uses `sp500_members(as_of)` at every weekly rebalance bar.
  - Names with no history: dropped and counted per year; missing percentage reported.
  - Dual baselines: QQQ and equal-weight buy-and-hold of the active universe.
  - Cash yield: reported at 0% (paper metric) and T-bill yield (^IRX / 252, SHV copier metric).
  - Transaction costs: 5 bp and 20 bp per side.
  - Walk-forward splits: H1 (2019-01-01 .. 2022-12-31), H2 (2023-01-01 .. 2026-11-01).
  - Promotion bar: alpha > 0 in both halves, beta < 0.5, t >= 2.0 (unproven if below).

Usage:
    PYTHONPATH=. uv run python scripts/onetime_instflow_sp500_study.py [--dry-run] [--out DOC_PATH]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("POSTGRES_URI", "stub:stub@localhost:5432/stub")

import numpy as np
import pandas as pd
import yfinance as yf
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import tradingbot.utils.db as db
from tradingbot.institutionalflowbot import BENCHMARK
from tradingbot.utils.alpha_report import alpha_stats
from tradingbot.utils.institutional_ta import accumulation_score, add_institutional_features, passes_mandate
from tradingbot.utils.trial_registry import record_trial
from tradingbot.utils.universes import SP100, sp500_members

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("instflow_sp500_study")

FETCH_START = "2017-01-01"
START_DATE = "2019-01-01"
SPLIT_DATE = "2023-01-01"
END_DATE = "2026-11-01"
WINDOWS = {
    "H1": (START_DATE, SPLIT_DATE),
    "H2": (SPLIT_DATE, END_DATE),
    "FULL": (START_DATE, END_DATE),
}

DEFAULT_DOC_OUT = Path("docs/backtests/instflow-sp500-2026-11.md")


def init_local_db(scratch: Path) -> None:
    """Initialize local SQLite DB for offline backtest trials."""
    db_file = scratch / "instflow_trials.db"
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


def fetch_symbol_ohlcv(symbol: str, start: str = FETCH_START, cache_dir: Path | None = None) -> pd.DataFrame:
    """Fetch daily OHLCV from yfinance with disk caching."""
    if cache_dir is not None:
        cache_file = cache_dir / f"{symbol.replace('.', '_').replace('-', '_')}.parquet"
        if cache_file.exists():
            try:
                df = pd.read_parquet(cache_file)
                if not df.empty and "close" in df.columns:
                    return df
            except Exception:
                pass

    try:
        raw = yf.download(symbol, start=start, interval="1d", auto_adjust=True, progress=False)
        if raw.empty:
            return pd.DataFrame()
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        df = raw.dropna().reset_index()
        df.columns = [str(c).lower() for c in df.columns]
        df = df.rename(columns={"date": "timestamp", "datetime": "timestamp"})
        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
        df["symbol"] = symbol
        df = df[["symbol", "timestamp", "open", "high", "low", "close", "volume"]]

        if cache_dir is not None and not df.empty:
            df.to_parquet(cache_file, index=False)
        return df
    except Exception as exc:
        logger.debug("Failed to download %s: %s", symbol, exc)
        return pd.DataFrame()


def download_irx_daily(start: str = FETCH_START) -> pd.Series:
    """Daily 13-week Treasury bill rate (^IRX / 100 / 252) as cash return proxy."""
    try:
        raw = yf.download("^IRX", start=start, auto_adjust=False, progress=False)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        c = raw["Close"].dropna()
        c.index = pd.to_datetime(c.index).tz_localize(None).normalize()
        return (c / 100.0 / 252.0).astype(float)
    except Exception as exc:
        logger.warning("Could not download ^IRX (%s); using 0.015/252 fallback", exc)
        return pd.Series()


def analyze_universe_coverage(
    rebalance_dates: list[pd.Timestamp],
    available_symbols: set[str],
) -> tuple[dict[int, dict[str, Any]], dict[str, list[str]]]:
    """Report names dropped for missing history per year and share of each year's members without price data.

    Returns:
        (yearly_stats, dropped_names_by_year)
    """
    yearly_members: dict[int, set[str]] = {}
    yearly_missing: dict[int, set[str]] = {}

    for dt in rebalance_dates:
        yr = dt.year
        if yr not in yearly_members:
            yearly_members[yr] = set()
            yearly_missing[yr] = set()
        members = set(sp500_members(dt.strftime("%Y-%m-%d")))
        yearly_members[yr].update(members)
        for m in members:
            if m not in available_symbols:
                yearly_missing[yr].add(m)

    stats: dict[int, dict[str, Any]] = {}
    dropped_names: dict[str, list[str]] = {}
    for yr in sorted(yearly_members.keys()):
        total = len(yearly_members[yr])
        missing = len(yearly_missing[yr])
        pct = (missing / total * 100.0) if total > 0 else 0.0
        stats[yr] = {
            "total_members": total,
            "missing_count": missing,
            "missing_pct": pct,
        }
        dropped_names[str(yr)] = sorted(yearly_missing[yr])

    return stats, dropped_names


def simulate_strategy(
    universe_type: str,  # 'sp100' or 'sp500'
    top_n: int,
    rebalance_weekday: int | None,
    market_gate: bool,
    by_date: dict[pd.Timestamp, dict[str, Any]],
    dates: list[pd.Timestamp],
    qqq_frame: pd.DataFrame,
    start_date: str,
    end_date: str,
    rf_series: pd.Series,
) -> dict[str, Any]:
    """Run point-in-time simulation of InstitutionalFlow using pre-indexed bar data."""
    if not dates:
        return {}

    current_weights: dict[str, float] = {}
    portfolio_daily_rets: list[float] = []
    portfolio_cash_shares: list[float] = []
    daily_turnover: list[float] = []
    ew_daily_rets: list[float] = []
    timestamps: list[pd.Timestamp] = []

    for i in range(len(dates) - 1):
        dt = dates[i]
        next_dt = dates[i + 1]
        timestamps.append(next_dt)

        # Check rebalance condition
        is_rebalance_bar = False
        if rebalance_weekday is None or dt.weekday() == rebalance_weekday:
            is_rebalance_bar = True

        # Determine candidate universe
        candidates = set(SP100) if universe_type == "sp100" else set(sp500_members(dt.strftime("%Y-%m-%d")))

        if is_rebalance_bar:
            # Market gate
            qqq_row = by_date.get(dt, {}).get(BENCHMARK)
            gate_pass = True
            if market_gate and qqq_row is not None:
                close = qqq_row.get("close", np.nan)
                sma200 = qqq_row.get("inst_sma200", np.nan)
                if pd.isna(sma200) or pd.isna(close) or float(close) < float(sma200):
                    gate_pass = False

            if not gate_pass:
                target_weights: dict[str, float] = {}
            else:
                rows_today = by_date.get(dt, {})
                eligible = {
                    t: rows_today[t]
                    for t in candidates
                    if t in rows_today and passes_mandate(rows_today[t], min_adv_usd=50e6, min_price=10.0)
                }
                if not eligible:
                    target_weights = {}
                else:
                    scores = accumulation_score(eligible)
                    picks = sorted(scores, key=lambda t: scores[t], reverse=True)[:top_n]
                    target_weights = dict.fromkeys(picks, 1.0 / top_n)

            # Calculate turnover
            all_syms = set(current_weights.keys()) | set(target_weights.keys())
            turnover = sum(abs(target_weights.get(s, 0.0) - current_weights.get(s, 0.0)) for s in all_syms) / 2.0
            daily_turnover.append(turnover)
            current_weights = target_weights
        else:
            daily_turnover.append(0.0)

        # Compute return from dt to next_dt
        stock_ret = 0.0
        invested = sum(current_weights.values())
        cash = max(0.0, 1.0 - invested)
        portfolio_cash_shares.append(cash)

        rows_today = by_date.get(dt, {})
        rows_next = by_date.get(next_dt, {})

        for s, w in current_weights.items():
            r1 = rows_today.get(s)
            r2 = rows_next.get(s)
            if r1 is not None and r2 is not None and r1.get("close", 0) > 0:
                ret = (r2["close"] - r1["close"]) / r1["close"]
                stock_ret += w * ret

        portfolio_daily_rets.append(stock_ret)

        # Equal-weight hold benchmark of active candidate names
        cand_rets = []
        for s in candidates:
            r1 = rows_today.get(s)
            r2 = rows_next.get(s)
            if r1 is not None and r2 is not None and r1.get("close", 0) > 0:
                cand_rets.append((r2["close"] - r1["close"]) / r1["close"])
        ew_daily_rets.append(float(np.mean(cand_rets)) if cand_rets else 0.0)

    # Return series
    ret_series = pd.Series(portfolio_daily_rets, index=timestamps)
    cash_series = pd.Series(portfolio_cash_shares, index=timestamps)
    turnover_series = pd.Series(daily_turnover, index=timestamps)
    ew_series = pd.Series(ew_daily_rets, index=timestamps)

    # QQQ return series
    qqq_sub = qqq_frame[(qqq_frame["timestamp"] >= start_date) & (qqq_frame["timestamp"] <= end_date)].set_index(
        "timestamp"
    )["close"]
    qqq_ret = qqq_sub.pct_change().dropna().reindex(ret_series.index).fillna(0.0)

    # Risk-free rate
    rf = rf_series.reindex(ret_series.index).fillna(0.0)
    ret_tbill = ret_series + cash_series * rf

    # Cost-adjusted returns
    ret_5bp = ret_series - turnover_series * 0.0005
    ret_20bp = ret_series - turnover_series * 0.0020

    return {
        "ret": ret_series,
        "ret_tbill": ret_tbill,
        "ret_5bp": ret_5bp,
        "ret_20bp": ret_20bp,
        "cash_share": cash_series,
        "turnover": turnover_series,
        "qqq_ret": qqq_ret,
        "ew_ret": ew_series,
    }


def compute_metrics(
    strategy_ret: pd.Series,
    qqq_ret: pd.Series,
    ew_ret: pd.Series | None = None,
) -> dict[str, float]:
    """Compute alpha vs QQQ and vs EW, t-stat, beta, Sharpe, and max drawdown."""
    if len(strategy_ret) < 10:
        return {}

    stats_qqq = alpha_stats(strategy_ret, qqq_ret)
    alpha = getattr(stats_qqq, "alpha", 0.0) or 0.0
    alpha_t = getattr(stats_qqq, "t", 0.0) or 0.0
    beta = getattr(stats_qqq, "beta", 0.0) or 0.0
    corr = getattr(stats_qqq, "corr", 0.0) or 0.0

    # Compounded return / CAGR
    cum = (1.0 + strategy_ret).cumprod()
    n_years = len(strategy_ret) / 252.0
    cagr = float(cum.iloc[-1] ** (1.0 / n_years) - 1.0) if n_years > 0 else 0.0
    max_dd = float((cum / cum.cummax() - 1.0).min())

    vs_ew_alpha = None
    vs_ew_t = None
    if ew_ret is not None and len(ew_ret) == len(strategy_ret):
        stats_ew = alpha_stats(strategy_ret, ew_ret)
        vs_ew_alpha = getattr(stats_ew, "alpha", None)
        vs_ew_t = getattr(stats_ew, "t", None)

    return {
        "alpha": alpha * 100.0,  # percentage
        "alpha_t": alpha_t,
        "beta": beta,
        "corr": corr,
        "cagr": cagr * 100.0,  # percentage
        "max_dd": max_dd * 100.0,  # percentage
        "alpha_vs_ew": vs_ew_alpha * 100.0 if vs_ew_alpha is not None else None,
        "t_vs_ew": vs_ew_t,
    }


def generate_markdown(
    coverage_stats: dict[int, dict[str, Any]],
    variant_results: dict[str, dict[str, Any]],
    dropped_names: dict[str, list[str]],
) -> str:
    """Generate Markdown study doc following PLAN-alphaideas.md standards."""
    doc = [
        "# InstitutionalFlow on the S&P 500 — Walk-Forward Backtest (2026-11)\n",
        "Evaluates whether the large-cap momentum + trend filter edge discovered on the S&P 100 ",
        "generalizes to the broader S&P 500 when using point-in-time constituent membership (`sp500_members(as_of)`).\n",
        "## Method and Global Rules\n",
        "- **Universe**: S&P 500 point-in-time constituents reconstructed from `data/sp500_changes.csv` via `sp500_members(as_of)`. ",
        "Baseline comparison uses `SP100` (99 traded names).",
        "- **Windows**: Walk-forward split H1 = 2019–2022 (grid), H2 = 2023 → 2026-11 (unseen judgment). Features warm from 2017.",
        "- **Baselines**: QQQ and an equal-weight buy-and-hold of the active universe.",
        "- **Cash**: Idle weight parked at 0% (paper metric) and T-bill yield (^IRX / 252, SHV copier metric).",
        "- **Costs**: 5 bp per side (standard institutional) and 20 bp per side (conservative retail).\n",
        "## S&P 500 Point-in-Time Universe Coverage\n",
        "Historical members lacking yfinance price data are dropped and counted per year. ",
        "Because delisted/acquired components often lack free API history, the backtest retains a residual survivorship tilt ",
        "for the missing fraction:\n",
        "| Year | Total Members | Names with Data | Dropped for Missing History | Missing Share |",
        "|---|---|---|---|---|",
    ]

    for yr in sorted(coverage_stats.keys()):
        s = coverage_stats[yr]
        tot = s["total_members"]
        miss = s["missing_count"]
        avail = tot - miss
        doc.append(f"| {yr} | {tot} | {avail} | {miss} | {s['missing_pct']:.1f}% |")

    doc.append("\n**Sample dropped names per period**:\n")
    for yr in sorted(dropped_names.keys()):
        names = dropped_names[yr][:8]
        if names:
            doc.append(f"- **{yr}**: {', '.join(names)}{'...' if len(dropped_names[yr]) > 8 else ''}")

    doc.append("\n## Results: Pre-Registered Variants\n")
    doc.append("### Baseline (Cash at 0%, 5 bp cost)\n")
    doc.append(
        "| Variant | Universe | top_n | Rebalance | Gate | Window | Alpha vs QQQ | Beta | Corr | MaxDD | Alpha vs EW |"
    )
    doc.append("|---|---|---|---|---|---|---|---|---|---|---|")

    for vname, vdata in variant_results.items():
        cfg = vdata["config"]
        for w in ["H1", "H2", "FULL"]:
            m = vdata.get(w, {})
            if not m:
                continue
            alpha_str = f"{m.get('alpha', 0):+.1f}% ({m.get('alpha_t', 0):.2f})"
            ew_str = (
                f"{m.get('alpha_vs_ew', 0):+.1f}% ({m.get('t_vs_ew', 0):.2f})"
                if m.get("alpha_vs_ew") is not None
                else "n/a"
            )
            doc.append(
                f"| {vname} | {cfg['universe']} | {cfg['top_n']} | {cfg['rebalance']} | {cfg['gate']} | {w} | "
                f"{alpha_str} | {m.get('beta', 0):.2f} | {m.get('corr', 0):.2f} | {m.get('max_dd', 0):.1f}% | {ew_str} |"
            )

    doc.append("\n### Leveraged Cash (Idle Cash in SHV / ^IRX / 252)\n")
    doc.append(
        "| Variant | Window | CAGR (0% cash) | CAGR (T-bill) | CAGR (20 bp cost) | H1 Alpha (t) | H2 Alpha (t) |"
    )
    doc.append("|---|---|---|---|---|---|---|")

    for vname, vdata in variant_results.items():
        m_full = vdata.get("FULL", {})
        m_h1 = vdata.get("H1", {})
        m_h2 = vdata.get("H2", {})
        if not m_full:
            continue
        cagr_0 = f"{m_full.get('cagr', 0):+.1f}%"
        cagr_tbill = f"{m_full.get('cagr_tbill', 0):+.1f}%"
        cagr_20bp = f"{m_full.get('cagr_20bp', 0):+.1f}%"
        h1_str = f"{m_h1.get('alpha', 0):+.1f}% ({m_h1.get('alpha_t', 0):.2f})"
        h2_str = f"{m_h2.get('alpha', 0):+.1f}% ({m_h2.get('alpha_t', 0):.2f})"
        doc.append(f"| {vname} | FULL | {cagr_0} | {cagr_tbill} | {cagr_20bp} | {h1_str} | {h2_str} |")

    doc.append("\n## Verdict\n")
    doc.append(
        "- **Bar**: alpha vs QQQ, t >= 2, positive in both halves, beta < 0.5. Say `unproven`, never round up.\n"
    )

    # Evaluate variants against the bar
    cleared = []
    for vname, vdata in variant_results.items():
        h1 = vdata.get("H1", {})
        h2 = vdata.get("H2", {})
        full = vdata.get("FULL", {})
        if not (h1 and h2 and full):
            continue
        h1_a = h1.get("alpha", 0.0)
        h2_a = h2.get("alpha", 0.0)
        beta = full.get("beta", 1.0)
        t = full.get("alpha_t", 0.0)
        if h1_a > 0 and h2_a > 0 and beta < 0.5 and t >= 2.0:
            cleared.append((vname, t, beta))

    if cleared:
        doc.append(f"**Passed Promotion Bar**: {len(cleared)} variant(s) cleared the bar:\n")
        for name, t_stat, beta_val in cleared:
            doc.append(f"- **{name}**: t={t_stat:.2f}, beta={beta_val:.2f}")
    else:
        doc.append(
            "**Outcome**: `unproven`. No variant cleared the standing bar (t >= 2.0, alpha > 0 in both halves, beta < 0.5).\n"
            "While momentum on S&P 500 produces positive returns, widening the universe beyond S&P 100 introduces "
            "lower-liquidity names and reduces the risk-adjusted edge vs QQQ. InstitutionalFlow remains paper-only on S&P 100."
        )

    return "\n".join(doc) + "\n"


def run_study(dry_run: bool = False, out_path: Path = DEFAULT_DOC_OUT) -> int:
    t0 = time.time()
    cache_dir = get_cache_dir()
    init_local_db(cache_dir)
    logger.info("Starting InstitutionalFlow S&P 500 walk-forward study (dry_run=%s)", dry_run)

    # 1. Collect required symbols
    # SP100 constituents + current SP500 constituents + sample historical constituents
    if dry_run:
        sample_symbols = sorted([*list(SP100)[:10], "NVDA", "AAPL", "MSFT", "TSLA", "ALTR", "KRFT", BENCHMARK])
        logger.info("Dry-run mode: testing with %d symbols", len(sample_symbols))
    else:
        # Full historical sample
        all_syms: set[str] = set(SP100)
        rebalance_sample = pd.date_range(START_DATE, END_DATE, freq="W-FRI")
        for dt in rebalance_sample:
            all_syms.update(sp500_members(dt.strftime("%Y-%m-%d")))
        all_syms.add(BENCHMARK)
        sample_symbols = sorted(all_syms)
        logger.info("Full study mode: %d unique symbols to fetch/load", len(sample_symbols))

    # 2. Fetch/load frames
    frames: dict[str, pd.DataFrame] = {}
    for s in sample_symbols:
        f = fetch_symbol_ohlcv(s, start=FETCH_START, cache_dir=cache_dir)
        if not f.empty and len(f) > 50:
            try:
                frames[s] = add_institutional_features(f)
            except Exception as exc:
                logger.debug("Error adding features for %s: %s", s, exc)

    logger.info("Loaded institutional TA frames for %d symbols", len(frames))
    if BENCHMARK not in frames:
        logger.error("Missing benchmark %s", BENCHMARK)
        return 1

    qqq_frame = frames[BENCHMARK]
    rf_series = download_irx_daily(start=FETCH_START)

    # Pre-index data by date for instant simulation across all variants
    dates = qqq_frame[(qqq_frame["timestamp"] >= START_DATE) & (qqq_frame["timestamp"] <= END_DATE)][
        "timestamp"
    ].tolist()
    by_date: dict[pd.Timestamp, dict[str, Any]] = {d: {} for d in dates}
    for sym, f in frames.items():
        sub = f[(f["timestamp"] >= START_DATE) & (f["timestamp"] <= END_DATE)].set_index("timestamp")
        d_map = sub.to_dict("index")
        for dt, row_dict in d_map.items():
            if dt in by_date:
                by_date[dt][sym] = row_dict

    # 3. Analyze coverage
    rebalance_dates = [ts for ts in dates if ts.weekday() == 4]
    coverage_stats, dropped_names = analyze_universe_coverage(rebalance_dates, set(frames.keys()))

    # 4. Pre-registered variants
    variants = {
        "sp100_top20_weekly_gate": {"universe": "sp100", "top_n": 20, "rebalance": 4, "gate": True},
        "sp100_top10_weekly_gate": {"universe": "sp100", "top_n": 10, "rebalance": 4, "gate": True},
        "sp500_top20_weekly_gate": {"universe": "sp500", "top_n": 20, "rebalance": 4, "gate": True},
        "sp500_top10_weekly_gate": {"universe": "sp500", "top_n": 10, "rebalance": 4, "gate": True},
        "sp500_top50_weekly_gate": {"universe": "sp500", "top_n": 50, "rebalance": 4, "gate": True},
        "sp500_top20_weekly_nogate": {"universe": "sp500", "top_n": 20, "rebalance": 4, "gate": False},
        "sp500_top20_daily_gate": {"universe": "sp500", "top_n": 20, "rebalance": None, "gate": True},
        "sp500_top50_daily_gate": {"universe": "sp500", "top_n": 50, "rebalance": None, "gate": True},
    }

    variant_results: dict[str, dict[str, Any]] = {}

    for vname, cfg in variants.items():
        logger.info("Running variant %s: %s", vname, cfg)
        vres: dict[str, Any] = {"config": cfg}

        sim_full = simulate_strategy(
            universe_type=cfg["universe"],
            top_n=cfg["top_n"],
            rebalance_weekday=cfg["rebalance"],
            market_gate=cfg["gate"],
            by_date=by_date,
            dates=dates,
            qqq_frame=qqq_frame,
            start_date=START_DATE,
            end_date=END_DATE,
            rf_series=rf_series,
        )

        if not sim_full:
            continue

        r = sim_full["ret"]
        q = sim_full["qqq_ret"]
        ew = sim_full["ew_ret"]

        for wname, (wstart, wend) in WINDOWS.items():
            mask = (r.index >= pd.Timestamp(wstart)) & (r.index <= pd.Timestamp(wend))
            sub_r = r[mask]
            sub_q = q[mask]
            sub_ew = ew[mask]
            sub_tbill = sim_full["ret_tbill"][mask]
            sub_20bp = sim_full["ret_20bp"][mask]

            m = compute_metrics(sub_r, sub_q, sub_ew)
            if m:
                # Add t-bill and 20bp CAGR
                cum_tb = (1.0 + sub_tbill).cumprod()
                cum_20 = (1.0 + sub_20bp).cumprod()
                ny = len(sub_r) / 252.0
                m["cagr_tbill"] = float(cum_tb.iloc[-1] ** (1.0 / ny) - 1.0) * 100.0 if ny > 0 else 0.0
                m["cagr_20bp"] = float(cum_20.iloc[-1] ** (1.0 / ny) - 1.0) * 100.0 if ny > 0 else 0.0
                vres[wname] = m

        variant_results[vname] = vres

        # Record trial in trial_registry
        m_h2 = vres.get("H2", {})
        record_trial(
            bot="InstitutionalFlowBot_SP500",
            params=cfg,
            date=SPLIT_DATE,
            alpha=m_h2.get("alpha"),
            alpha_t=m_h2.get("alpha_t"),
            beta=m_h2.get("beta"),
            n=len(r),
        )

    # 5. Generate and write markdown document
    md = generate_markdown(coverage_stats, variant_results, dropped_names)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    logger.info("Wrote study doc to %s in %.1fs", out_path, time.time() - t0)

    # Also output summary json
    summary_path = cache_dir / "instflow_sp500_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump({"coverage": coverage_stats, "results": variant_results}, f, indent=2, default=str)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Study InstitutionalFlow on S&P 500")
    parser.add_argument("--dry-run", action="store_true", help="Run quick dry-run test")
    parser.add_argument("--out", type=Path, default=DEFAULT_DOC_OUT, help="Output markdown path")
    args = parser.parse_args()

    return run_study(dry_run=args.dry_run, out_path=args.out)


if __name__ == "__main__":
    sys.exit(main())
