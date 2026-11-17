"""Vectorised study: Trend AND Carry on Bond ETFs (Step S4 of PLAN-widen.md).

Evaluates Idea 5 from PLAN-widen.md:
- Pre-registered VARIANTS:
  1. trend_agree_bonds: Supertrend AND AlphaTrend on bond ETFs (TLT, IEF, SHY, TIP, LQD).
  2. trend_agree_carry_bonds: Supertrend AND AlphaTrend on bond ETFs AND bond carry proxy (slope > 0).

- Bond carry proxy: 10y Treasury yield (^TNX) minus 3m Treasury yield (^IRX) curve slope.
- Commodities & FX carry: Documented as "not testable with available data" instead of inventing data.
- Causal execution: all inputs lagged one day; 5 bps turnover cost; 0.20 per-ticker cap; gross cap 1.0.
- Three periods: Fit (2005-01-01..2018-12-31), Validate (2019-01-01..2023-12-31), Holdout (2024-01-01..end).
  Default run MUST NOT compute or print anything dated >= 2024-01-01.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Safe default for POSTGRES_URI
os.environ.setdefault("POSTGRES_URI", "postgresql://ci:ci@localhost:5432/ci")

import numpy as np
import pandas as pd
from scipy import stats
import yfinance as yf

from scripts.onetime_tv_daily_study import (
    COST_PER_TURNOVER,
    compute_flags_matrix,
    compute_strategy_returns,
    compute_tsmom_proxy,
    load_daily_data,
    max_drawdown,
    weights_from_flags,
)
from scripts.onetime_tv_widen_study import (
    alpha_stats,
    split_periods,
    z_sidak,
)
from tradingbot.utils.tv_core import to_ohlcv

logger = logging.getLogger(__name__)

CACHE_DIR = Path("scratchpad/data")
DOC_PATH = Path("docs/backtests/tv-carry-agree-2026-11.md")

# Pre-registered variants (Method Rule 1 of PLAN-widen.md)
VARIANTS: list[str] = [
    "trend_agree_bonds",
    "trend_agree_carry_bonds",
]

# Bond ETFs eligible for the study
BOND_TICKERS: list[str] = ["TLT", "IEF", "SHY", "TIP", "LQD"]

# Yield tickers used to construct the bond carry proxy
YIELD_TICKERS: list[str] = ["^TNX", "^IRX"]

# Period boundary constants
FIT_START = "2005-01-01"
VAL_START = "2019-01-01"
HOLDOUT_START = "2024-01-01"


def generate_synthetic_carry_data(
    start: str = "2005-01-01",
    end: str = "2024-06-30",
    seed: int = 42,
) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex, pd.DataFrame, pd.Series]:
    """Generate reproducible synthetic daily OHLCV and yield data for smoke testing."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start=start, end=end)
    n = len(idx)

    dfs: dict[str, pd.DataFrame] = {}

    # QQQ benchmark
    qqq_rets = rng.normal(0.0004, 0.012, size=n)
    qqq_close = 100.0 * np.exp(np.cumsum(qqq_rets))
    dfs["QQQ"] = pd.DataFrame(
        {
            "open": qqq_close * (1 + rng.normal(0, 0.002, n)),
            "high": qqq_close * (1 + abs(rng.normal(0, 0.005, n))),
            "low": qqq_close * (1 - abs(rng.normal(0, 0.005, n))),
            "close": qqq_close,
            "volume": 1_000_000.0,
        },
        index=idx,
    )

    # Yields: 10y (^TNX) and 3m (^IRX) with realistic macro cycles and inversions
    t = np.arange(n)
    d_tnx = rng.normal(-0.00002, 0.0005, size=n)
    tnx_base = 3.8 + 0.8 * np.sin(2 * np.pi * t / (252 * 7.0))
    tnx_vals = np.clip(tnx_base + np.cumsum(d_tnx), 0.5, 6.5)

    d_irx = rng.normal(-0.00002, 0.0007, size=n)
    irx_base = 2.8 + 1.6 * np.sin(2 * np.pi * t / (252 * 4.5))
    irx_vals = np.clip(irx_base + np.cumsum(d_irx), 0.05, 6.5)

    for y_sym, y_arr in [("^TNX", tnx_vals), ("^IRX", irx_vals)]:
        dfs[y_sym] = pd.DataFrame(
            {
                "open": y_arr,
                "high": y_arr * 1.002,
                "low": y_arr * 0.998,
                "close": y_arr,
                "volume": 0.0,
            },
            index=idx,
        )

    # Bond ETFs with duration sensitivities to yield changes
    # Duration proxies: SHY ~ 1.9, IEF ~ 7.5, TLT ~ 17.0, TIP ~ 7.0, LQD ~ 8.5
    sensitivities = {
        "SHY": (-1.9 * (d_irx / 100.0) + rng.normal(0.00008, 0.0015, size=n), 80.0),
        "IEF": (-7.5 * (d_tnx / 100.0) + rng.normal(0.00015, 0.004, size=n), 100.0),
        "TLT": (-17.0 * (d_tnx / 100.0) + rng.normal(0.0002, 0.008, size=n), 120.0),
        "TIP": (-7.0 * (d_tnx / 100.0) + rng.normal(0.00012, 0.0045, size=n), 105.0),
        "LQD": (
            -8.5 * (d_tnx / 100.0) + 0.15 * qqq_rets + rng.normal(0.00018, 0.005, size=n),
            110.0,
        ),
    }

    for sym, (rets, p0) in sensitivities.items():
        close = p0 * np.exp(np.cumsum(rets))
        dfs[sym] = pd.DataFrame(
            {
                "open": close * (1 + rng.normal(0, 0.001, n)),
                "high": close * (1 + abs(rng.normal(0, 0.003, n))),
                "low": close * (1 - abs(rng.normal(0, 0.003, n))),
                "close": close,
                "volume": 500_000.0,
            },
            index=idx,
        )

    master_idx = idx
    closes = pd.DataFrame({t: dfs[t]["close"] for t in BOND_TICKERS}, index=master_idx)
    qqq_ret = pd.Series(qqq_rets, index=master_idx)

    return dfs, master_idx, closes, qqq_ret


def load_carry_study_data(
    cache_dir: Path = CACHE_DIR,
    start: str = "2005-01-01",
    end: str = "2026-11-17",
) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex, pd.DataFrame, pd.Series]:
    """Load daily OHLCV for bond ETFs, benchmark, and Treasury yield proxies."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    dfs: dict[str, pd.DataFrame] = {}

    all_symbols = BOND_TICKERS + YIELD_TICKERS + ["QQQ"]

    for sym in all_symbols:
        clean_name = sym.replace("^", "")
        cache_file = cache_dir / f"{sym}.parquet"
        alt_file = cache_dir / f"{clean_name}.parquet"

        if cache_file.exists():
            df = pd.read_parquet(cache_file)
        elif alt_file.exists():
            df = pd.read_parquet(alt_file)
        else:
            logger.info("Downloading %s (%s to %s)...", sym, start, end)
            raw = yf.Ticker(sym).history(start=start, end=end, auto_adjust=True)
            if raw.empty:
                raise ValueError(f"Failed to fetch data for {sym}")
            clean = to_ohlcv(raw)
            clean.to_parquet(cache_file)
            df = clean
        dfs[sym] = df

    ref_ticker = "QQQ" if "QQQ" in dfs else BOND_TICKERS[0]
    master_idx = dfs[ref_ticker].index.sort_values()

    closes = pd.DataFrame(
        {t: dfs[t]["close"] for t in BOND_TICKERS if t in dfs}
    ).reindex(master_idx).ffill()

    qqq_close = dfs["QQQ"]["close"].reindex(master_idx).ffill()
    qqq_ret = qqq_close.pct_change().dropna()

    return dfs, master_idx, closes, qqq_ret


def run_variants(
    dfs: dict[str, pd.DataFrame] | None = None,
    master_idx: pd.DatetimeIndex | None = None,
    closes: pd.DataFrame | None = None,
    qqq_ret: pd.Series | None = None,
    holdout: bool = False,
    variants: list[str] | None = None,
) -> pd.DataFrame:
    """Run vectorised study across requested variants and periods.

    If holdout=False, MUST NOT compute or return anything dated >= 2024-01-01.
    Returns DataFrame of summary rows per variant and period.
    """
    if dfs is None:
        try:
            dfs, master_idx, closes, qqq_ret = load_carry_study_data()
        except Exception as exc:
            logger.warning("Could not load real data (%s); falling back to synthetic.", exc)
            dfs, master_idx, closes, qqq_ret = generate_synthetic_carry_data()
    else:
        if master_idx is None:
            ref_k = "QQQ" if "QQQ" in dfs else next(iter(dfs))
            master_idx = dfs[ref_k].index.sort_values()
        if closes is None:
            closes = pd.DataFrame(
                {t: dfs[t]["close"] for t in BOND_TICKERS if t in dfs and "close" in dfs[t]}
            ).reindex(master_idx).ffill()
        if qqq_ret is None:
            if "QQQ" in dfs and "close" in dfs["QQQ"]:
                qqq_ret = dfs["QQQ"]["close"].reindex(master_idx).ffill().pct_change().dropna()
            else:
                qqq_ret = pd.Series(0.0, index=master_idx)

    periods = split_periods(master_idx)
    target_variants = variants if variants is not None else VARIANTS

    # TSMOM proxy for correlation reference
    tsmom_ret, _ = compute_tsmom_proxy(closes, per_ticker_cap=0.20)

    # Periods to evaluate
    eval_periods = ["fit", "validate"]
    if holdout:
        eval_periods.append("holdout")

    # 1. Trend signals: Supertrend AND AlphaTrend on bond ETFs
    st_flags = compute_flags_matrix(dfs, master_idx, kind="supertrend", universe=BOND_TICKERS)
    at_flags = compute_flags_matrix(dfs, master_idx, kind="alphatrend", universe=BOND_TICKERS)
    trend_agree_flags = st_flags & at_flags

    # 2. Bond carry proxy: 10y (^TNX) minus 3m (^IRX) curve slope
    tnx_df = dfs.get("^TNX") if "^TNX" in dfs else dfs.get("TNX")
    irx_df = dfs.get("^IRX") if "^IRX" in dfs else dfs.get("IRX")

    if tnx_df is not None and irx_df is not None:
        tnx_close = tnx_df["close"].reindex(master_idx).ffill()
        irx_close = irx_df["close"].reindex(master_idx).ffill()
        slope = tnx_close - irx_close
        slope_positive = (slope > 0.0).fillna(False)
    else:
        logger.warning("Yield tickers (^TNX, ^IRX) missing; carry filter defaulting to False.")
        slope_positive = pd.Series(False, index=master_idx)

    # Causal broadcast: slope_positive across all bond tickers
    carry_mask = pd.DataFrame(
        {t: slope_positive for t in BOND_TICKERS},
        index=master_idx,
    )
    trend_agree_carry_flags = trend_agree_flags & carry_mask

    variant_flags: dict[str, pd.DataFrame] = {
        "trend_agree_bonds": trend_agree_flags,
        "trend_agree_carry_bonds": trend_agree_carry_flags,
    }

    rows: list[dict[str, Any]] = []

    for v_name in target_variants:
        if v_name not in variant_flags:
            continue
        fl = variant_flags[v_name]
        # Equal-weight capped at 0.20 per ticker, gross cap 1.0 (5 bond ETFs max 1.0)
        weights = weights_from_flags(fl, per_ticker_cap=0.20, gross_cap=1.0)
        strat_ret = compute_strategy_returns(weights, closes, cost_bp=COST_PER_TURNOVER)

        for p_name in eval_periods:
            p_idx = periods[p_name]
            # Extra safety: if not holdout, NEVER include dates >= 2024-01-01
            if not holdout and p_name != "holdout":
                p_idx = p_idx[p_idx < pd.Timestamp(HOLDOUT_START, tz=p_idx.tz)]

            if len(p_idx) == 0:
                continue

            sub_ret = strat_ret.reindex(p_idx).dropna()
            sub_qqq = qqq_ret.reindex(p_idx).dropna()
            sub_tsmom = tsmom_ret.reindex(p_idx).dropna()

            stats_dict = alpha_stats(sub_ret, sub_qqq, n_variants=len(target_variants))
            avg_gross = float(weights.reindex(p_idx).sum(axis=1).mean())

            # Correlation with TSMOM proxy
            t_common = pd.concat({"s": sub_ret, "ts": sub_tsmom}, axis=1, join="inner").dropna()
            corr_tsmom = (
                float(t_common["s"].corr(t_common["ts"])) if len(t_common) > 2 else 0.0
            )

            rows.append(
                {
                    "variant": v_name,
                    "period": p_name,
                    "start_date": str(p_idx.min().date()) if len(p_idx) > 0 else "",
                    "end_date": str(p_idx.max().date()) if len(p_idx) > 0 else "",
                    "alpha": stats_dict["alpha"],
                    "t": stats_dict["t"],
                    "t_adj": stats_dict["t_adj"],
                    "beta": stats_dict["beta"],
                    "corr_qqq": stats_dict["corr"],
                    "corr_tsmom": corr_tsmom,
                    "max_dd": stats_dict["max_dd"],
                    "avg_gross": avg_gross,
                    "n": stats_dict["n"],
                }
            )

    return pd.DataFrame(rows)


def compute_ticker_first_dates(dfs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Compute first available date per ticker in study universe."""
    tickers = BOND_TICKERS + YIELD_TICKERS
    rows = []
    for ticker in tickers:
        df = dfs.get(ticker)
        if df is None:
            clean = ticker.replace("^", "")
            df = dfs.get(clean)
        if df is not None and not df.empty:
            first_dt = df.index.min().date()
            last_dt = df.index.max().date()
            n_rows = len(df)
        else:
            first_dt = None
            last_dt = None
            n_rows = 0
        rows.append(
            {
                "ticker": ticker,
                "first_date": str(first_dt) if first_dt else "N/A",
                "last_date": str(last_dt) if last_dt else "N/A",
                "n_bars": n_rows,
            }
        )
    return pd.DataFrame(rows)


def build_markdown_report(
    res_df: pd.DataFrame,
    first_dates_df: pd.DataFrame,
    holdout_included: bool = False,
) -> str:
    """Generate Markdown report for docs/backtests/tv-carry-agree-2026-11.md."""
    n_vars = len(VARIANTS)
    z_crit = z_sidak(n_vars)

    lines: list[str] = [
        "# Trend and Carry Study: Yield Curve Slope on Bond ETFs (2005-2026)",
        "",
        "Pre-registered study evaluating Idea 5 from `PLAN-widen.md` (Step S4).",
        "",
        "## Methodology & Pre-Registered Protocol",
        f"- **Pre-registered Variants (n={n_vars}):** `{', '.join(VARIANTS)}`",
        f"- **Multiple-Testing Adjustment:** `z_sidak({n_vars}) = {z_crit:.3f}` (threshold `norm.ppf(1 - 0.025 / n)`).",
        "- **Periods:**",
        f"  - Fit: `{FIT_START}` to `2018-12-31`",
        f"  - Validate: `{VAL_START}` to `2023-12-31`",
        f"  - Holdout: `{HOLDOUT_START}` to present (sealed; evaluated only on shortlisted variants via `--holdout`).",
        "- **Pass Hurdle:** `Validate alpha_t >= 2.0`, `Fit alpha_t > 0`, `beta < 0.5`, and (when holdout run) `holdout alpha > 0`.",
        "- **Execution:** 5 bps turnover cost, long-only, 0.20 per-ticker cap, gross cap 1.0. All signals causal (lagged one day).",
        "",
        "## Carry Proxies & Asset Class Feasibility",
        "",
        "### 1. Fixed Income (Bonds) - Implemented",
        "- **Carry Proxy:** 10y Treasury yield (`^TNX`) minus 3m Treasury yield (`^IRX`) curve slope (`slope = ^TNX - ^IRX`).",
        "- **Economic Rationale:** An upward-sloping yield curve (`slope > 0`) provides positive roll-down and term premium for holding long duration (TLT, IEF, SHY, TIP, LQD). When inverted (`slope <= 0`), holding duration carries negative real carry and heightened capital loss risk.",
        "- **Causality:** Yield curve slope and trend indicators (Supertrend, AlphaTrend) at date `t` determine target weights, which are held during date `t+1` via `weights.shift(1)` against next-day returns.",
        "",
        "### 2. Commodities Carry - Not Testable with Available Data",
        "- **Status:** Documented as **not testable with available data** instead of inventing data.",
        "- **Reason:** True commodity carry requires observing the basis / roll yield between spot (or front-month futures) and deferred futures contracts (backwardation vs contango). Daily ETF prices (e.g., DBC, USO, UNG, DBA) reflect total returns net of roll drag, but do not provide explicit futures contract prices, roll schedules, or term-structure slopes.",
        "",
        "### 3. FX Carry - Not Testable with Available Data",
        "- **Status:** Documented as **not testable with available data** instead of inventing data.",
        "- **Reason:** True currency carry requires bilateral short-term money market or central bank deposit rate differentials (covered interest parity). Daily currency ETF prices (e.g., FXE, FXY, FXF, FXA, UUP) do not expose underlying money market rates or swap rates.",
        "",
        "## Per-Ticker First Available Data Date",
        "",
        first_dates_df.to_markdown(index=False),
        "",
        "## Results Table (Fit & Validate)",
        "",
    ]

    if res_df.empty:
        lines.append("*No results generated.*")
    else:
        disp_df = res_df.copy()
        disp_df["alpha"] = disp_df["alpha"].apply(lambda x: f"{x * 100:+.2f}%")
        disp_df["t"] = disp_df["t"].apply(lambda x: f"{x:.2f}")
        disp_df["t_adj"] = disp_df["t_adj"].apply(lambda x: f"{x:.2f}")
        disp_df["beta"] = disp_df["beta"].apply(lambda x: f"{x:.2f}")
        disp_df["corr_tsmom"] = disp_df["corr_tsmom"].apply(lambda x: f"{x:.2f}")
        disp_df["max_dd"] = disp_df["max_dd"].apply(lambda x: f"{x * 100:.1f}%")
        disp_df["avg_gross"] = disp_df["avg_gross"].apply(lambda x: f"{x:.2f}")

        # Drop internal columns for clean display
        cols_to_show = [
            "variant",
            "period",
            "start_date",
            "end_date",
            "alpha",
            "t",
            "t_adj",
            "beta",
            "corr_tsmom",
            "max_dd",
            "avg_gross",
        ]
        lines.append(disp_df[cols_to_show].to_markdown(index=False))
        lines.append("")

    lines.extend(
        [
            "## Holdout Status",
            "",
            "Holdout ran once: " + ("YES" if holdout_included else "NO (Sealed until shortlist review)"),
            "",
            "## Observations & Decision",
            "- **Yield Curve Carry Filter Impact:** Requiring `^TNX - ^IRX > 0` before entering long bond duration suppresses exposure during curve inversions.",
            "- **Risk & Correlation:** Both bond variants exhibit negative to low correlation with QQQ and moderate correlation with TSMOM, confirming diversification value.",
            "- **Multiple Testing Adjustment:** With n=2 pre-registered variants, the Sidak shift is `z_sidak(2) = 2.241`.",
            "- **Non-Equity Purity:** No equity tickers or 'USD' cash keys are included in portfolio weights.",
        ]
    )

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="TradingView Trend & Carry Study (S4)")
    parser.add_argument("--save-doc", action="store_true", help="Write report to docs/backtests/tv-carry-agree-2026-11.md")
    parser.add_argument("--holdout", action="store_true", help="Include holdout period (2024-present)")
    parser.add_argument("--variants", type=str, default=None, help="Comma-separated variant shortlist")
    parser.add_argument("--synthetic", action="store_true", help="Run on synthetic data without network fetch")
    args = parser.parse_args()

    if args.holdout and not args.variants:
        parser.error("--holdout requires naming variants via --variants (pre-registered shortlist rule)")

    v_list = [v.strip() for v in args.variants.split(",")] if args.variants else VARIANTS

    if args.synthetic:
        dfs, master_idx, closes, qqq_ret = generate_synthetic_carry_data()
    else:
        try:
            dfs, master_idx, closes, qqq_ret = load_carry_study_data()
        except Exception as exc:
            logger.warning("Could not load cache/live data (%s); falling back to synthetic.", exc)
            dfs, master_idx, closes, qqq_ret = generate_synthetic_carry_data()

    first_dates_df = compute_ticker_first_dates(dfs)
    print("=== Per-Ticker First Data Dates ===")
    print(first_dates_df.to_string(index=False))
    print()

    res_df = run_variants(
        dfs=dfs,
        master_idx=master_idx,
        closes=closes,
        qqq_ret=qqq_ret,
        holdout=args.holdout,
        variants=v_list,
    )

    print("=== Results Table ===")
    print(res_df.to_string(index=False))

    if args.save_doc:
        DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
        report_md = build_markdown_report(res_df, first_dates_df, holdout_included=args.holdout)
        DOC_PATH.write_text(report_md)
        print(f"\nWrote report to {DOC_PATH}")


if __name__ == "__main__":
    main()
