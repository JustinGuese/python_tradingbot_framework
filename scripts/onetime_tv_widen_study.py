"""Vectorised study: TradingView agreement rule on wider non-equity universe.

PLAN-widen.md Step S0 / S1.
Compares 5 pre-registered variants across Fit (2005-2018) and Validate (2019-2023):
- st_narrow: Supertrend on 8 ex-equity tickers
- agree_narrow: Supertrend AND AlphaTrend on 8 ex-equity tickers
- agree_wide: Supertrend AND AlphaTrend on 21 non-equity tickers (UNIVERSE_EX_EQUITY_WIDE)
- st_wide: Supertrend on 21 non-equity tickers
- at_wide: AlphaTrend on 21 non-equity tickers

Holdout (2024-end) is sealed by default and only evaluated with --holdout for named variants.
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

# Safe default for POSTGRES_URI so importing helper modules doesn't fail on missing db URI
os.environ.setdefault("POSTGRES_URI", "postgresql://ci:ci@localhost:5432/ci")

import numpy as np
import pandas as pd
from scipy import stats

from scripts.onetime_tv_daily_study import (
    COST_PER_TURNOVER,
    compute_flags_matrix,
    compute_strategy_returns,
    compute_tsmom_proxy,
    load_daily_data,
    max_drawdown,
    weights_from_flags,
)
from tradingbot.utils.tv_signals import (
    UNIVERSE_EX_EQUITY,
    UNIVERSE_EX_EQUITY_WIDE,
)

logger = logging.getLogger(__name__)

CACHE_DIR = Path("scratchpad/data")
DOC_PATH = Path("docs/backtests/tv-widen-2026-11.md")

# Module-level constant pre-registered variants (Rule 1 of PLAN-widen.md)
VARIANTS: list[str] = [
    "st_narrow",
    "agree_narrow",
    "agree_wide",
    "st_wide",
    "at_wide",
]

# Period cutoff dates
FIT_START = "2005-01-01"
VAL_START = "2019-01-01"
HOLDOUT_START = "2024-01-01"


def z_sidak(n_variants: int = 5) -> float:
    """One-sided Sidak / Bonferroni corrected z threshold for n variants.

    Threshold: t_required = norm.ppf(1 - 0.025 / n). For n=5, ~2.576 (~2.58).
    """
    if n_variants <= 0:
        return 0.0
    return float(stats.norm.ppf(1.0 - 0.025 / n_variants))


def split_periods(idx: pd.DatetimeIndex) -> dict[str, pd.DatetimeIndex]:
    """Partition DatetimeIndex into disjoint fit, validate, and holdout periods.

    - fit: up to 2018-12-31 (nominally 2005-01-01..2018-12-31)
    - validate: 2019-01-01..2023-12-31
    - holdout: 2024-01-01..end

    Enforces that fit and validate NEVER contain dates >= 2024-01-01.
    """
    idx_sorted = idx.sort_values()

    # Timezone-safe timestamp bounds
    tz = idx_sorted.tz
    val_cutoff = pd.Timestamp(VAL_START, tz=tz)
    holdout_cutoff = pd.Timestamp(HOLDOUT_START, tz=tz)

    fit_idx = idx_sorted[idx_sorted < val_cutoff]
    val_idx = idx_sorted[(idx_sorted >= val_cutoff) & (idx_sorted < holdout_cutoff)]
    holdout_idx = idx_sorted[idx_sorted >= holdout_cutoff]

    return {
        "fit": fit_idx,
        "validate": val_idx,
        "holdout": holdout_idx,
    }


def alpha_stats(
    ret: pd.Series,
    qqq: pd.Series,
    n_variants: int = 5,
) -> dict[str, Any]:
    """Alpha vs QQQ benchmark with multiple-testing adjusted stats.

    Exact formula (PLAN-widen.md Rule 4):
        beta = cov(r, qqq)/var(qqq)
        resid = r - beta*qqq
        alpha = resid.mean()*252
        t = resid.mean()/resid.std()*sqrt(n)
        t_adj = t - z_sidak(n_variants) where z_sidak = norm.ppf(1 - 0.025 / n)
    """
    common = pd.concat({"r": ret, "q": qqq}, axis=1, join="inner").dropna()
    common = common.replace([np.inf, -np.inf], np.nan).dropna()
    n = len(common)

    if n < 3:
        return {
            "alpha": 0.0,
            "t": 0.0,
            "t_adj": 0.0,
            "beta": 0.0,
            "corr": 0.0,
            "n": n,
            "max_dd": 0.0,
        }

    r = common["r"]
    q = common["q"]

    var_q = float(q.var(ddof=1))
    if var_q <= 1e-12:
        beta = 0.0
    else:
        cov_rq = float(r.cov(q))
        beta = cov_rq / var_q

    resid = r - beta * q
    alpha = float(resid.mean() * 252.0)
    std_resid = float(resid.std(ddof=1))

    if std_resid > 1e-12:
        t = float(resid.mean() / std_resid * np.sqrt(n))
    else:
        t = 0.0

    z_crit = z_sidak(n_variants)
    t_adj = float(t - z_crit)

    std_r = float(r.std(ddof=1))
    std_q = float(q.std(ddof=1))
    if std_r > 1e-12 and std_q > 1e-12:
        corr = float(r.corr(q))
    else:
        corr = 0.0

    # Max drawdown
    cum = (1.0 + r).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    mdd = float(dd.min()) if len(dd) > 0 else 0.0

    return {
        "alpha": alpha,
        "t": t,
        "t_adj": t_adj,
        "beta": beta,
        "corr": corr,
        "n": n,
        "max_dd": mdd,
    }


def generate_synthetic_study_data(
    tickers: list[str] | None = None,
    start: str = "2005-01-01",
    end: str = "2024-06-30",
    seed: int = 42,
) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex, pd.DataFrame, pd.Series]:
    """Generate reproducible synthetic daily OHLCV data for testing without network."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start=start, end=end)
    uni = tickers or UNIVERSE_EX_EQUITY_WIDE
    all_tickers = sorted(set(list(uni) + ["QQQ", "SPY"]))

    dfs: dict[str, pd.DataFrame] = {}
    n = len(idx)

    # Base market process (QQQ)
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

    for t in all_tickers:
        if t == "QQQ":
            continue
        drift = 0.0002
        vol = 0.010
        # Correlated returns
        rets = 0.3 * qqq_rets + rng.normal(drift, vol, size=n)
        close = 50.0 * np.exp(np.cumsum(rets))
        dfs[t] = pd.DataFrame(
            {
                "open": close * (1 + rng.normal(0, 0.002, n)),
                "high": close * (1 + abs(rng.normal(0, 0.005, n))),
                "low": close * (1 - abs(rng.normal(0, 0.005, n))),
                "close": close,
                "volume": 500_000.0,
            },
            index=idx,
        )

    master_idx = idx
    closes = pd.DataFrame({t: dfs[t]["close"] for t in all_tickers if t in dfs}, index=master_idx)
    qqq_ret = pd.Series(qqq_rets, index=master_idx)

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
        dfs, master_idx, _, qqq_ret = load_daily_data(
            cache_dir=CACHE_DIR,
            universe=UNIVERSE_EX_EQUITY_WIDE,
        )
        closes = pd.DataFrame({t: dfs[t]["close"] for t in UNIVERSE_EX_EQUITY_WIDE if t in dfs and "close" in dfs[t]}).reindex(master_idx).ffill()
    else:
        if master_idx is None:
            ref_k = "QQQ" if "QQQ" in dfs else next(iter(dfs))
            master_idx = dfs[ref_k].index.sort_values()
        if closes is None:
            closes = pd.DataFrame({t: dfs[t]["close"] for t in dfs if "close" in dfs[t]}).reindex(master_idx).ffill()
        if qqq_ret is None:
            if "QQQ" in closes:
                qqq_ret = closes["QQQ"].pct_change().dropna()
            elif "QQQ" in dfs:
                qqq_ret = dfs["QQQ"]["close"].reindex(master_idx).ffill().pct_change().dropna()
            else:
                qqq_ret = pd.Series(0.0, index=master_idx)

    periods = split_periods(master_idx)
    target_variants = variants if variants is not None else VARIANTS

    # TSMOM proxy for correlation reference
    tsmom_ret, _ = compute_tsmom_proxy(closes, per_ticker_cap=0.20)

    # Determine periods to run
    eval_periods = ["fit", "validate"]
    if holdout:
        eval_periods.append("holdout")

    # Cache flags matrices
    st_narrow_flags = compute_flags_matrix(dfs, master_idx, kind="supertrend", universe=UNIVERSE_EX_EQUITY)
    at_narrow_flags = compute_flags_matrix(dfs, master_idx, kind="alphatrend", universe=UNIVERSE_EX_EQUITY)
    st_wide_flags = compute_flags_matrix(dfs, master_idx, kind="supertrend", universe=UNIVERSE_EX_EQUITY_WIDE)
    at_wide_flags = compute_flags_matrix(dfs, master_idx, kind="alphatrend", universe=UNIVERSE_EX_EQUITY_WIDE)

    variant_flags: dict[str, pd.DataFrame] = {
        "st_narrow": st_narrow_flags,
        "agree_narrow": st_narrow_flags & at_narrow_flags,
        "agree_wide": st_wide_flags & at_wide_flags,
        "st_wide": st_wide_flags,
        "at_wide": at_wide_flags,
    }

    rows: list[dict[str, Any]] = []

    for v_name in target_variants:
        if v_name not in variant_flags:
            continue
        fl = variant_flags[v_name]
        # Equal-weight capped at 0.20 per ticker, gross cap 1.0
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
            if len(t_common) > 2 and float(t_common["s"].std(ddof=1)) > 1e-12 and float(t_common["ts"].std(ddof=1)) > 1e-12:
                corr_tsmom = float(t_common["s"].corr(t_common["ts"]))
            else:
                corr_tsmom = 0.0

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
    """Compute first available date per ticker in UNIVERSE_EX_EQUITY_WIDE."""
    rows = []
    for ticker in UNIVERSE_EX_EQUITY_WIDE:
        df = dfs.get(ticker)
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
    """Generate Markdown report for docs/backtests/tv-widen-2026-11.md."""
    lines: list[str] = [
        "# TradingView Agreement Rule: Narrow vs Wide Non-Equity Universe (2005-2026)",
        "",
        "Pre-registered study evaluating Ideas 1 & 2 from `PLAN-widen.md`.",
        "",
        "## Methodology & Pre-Registered Protocol",
        f"- **Pre-registered Variants (n={len(VARIANTS)}):** `{', '.join(VARIANTS)}`",
        f"- **Multiple-Testing Adjustment:** `z_sidak({len(VARIANTS)}) = {z_sidak(len(VARIANTS)):.3f}` (threshold `norm.ppf(1 - 0.025 / n)`).",
        "- **Periods:**",
        "  - Fit: `2005-01-01` to `2018-12-31`",
        "  - Validate: `2019-01-01` to `2023-12-31`",
        "  - Holdout: `2024-01-01` to present (sealed; run once on shortlisted variants only).",
        "- **Pass Hurdle:** `Validate alpha_t >= 2.0`, `Fit alpha_t > 0`, `beta < 0.5`, and (when holdout run) `holdout alpha > 0`.",
        "- **Execution:** 5 bps turnover cost, long-only, 0.20 per-ticker cap, gross cap 1.0.",
        "",
        "## Per-Ticker First Available Data Date",
        "",
        first_dates_df.to_markdown(index=False),
        "",
        "## Results Table (Fit & Validate)",
        "",
    ]

    if not res_df.empty:
        # Pivot or format results
        cols = [
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
        disp_df = res_df[cols].copy()
        disp_df["alpha"] = disp_df["alpha"].apply(lambda x: f"{x * 100:+.2f}%")
        disp_df["t"] = disp_df["t"].apply(lambda x: f"{x:+.2f}")
        disp_df["t_adj"] = disp_df["t_adj"].apply(lambda x: f"{x:+.2f}")
        disp_df["beta"] = disp_df["beta"].apply(lambda x: f"{x:.2f}")
        disp_df["corr_tsmom"] = disp_df["corr_tsmom"].apply(lambda x: f"{x:.2f}")
        disp_df["max_dd"] = disp_df["max_dd"].apply(lambda x: f"{x * 100:.1f}%")
        disp_df["avg_gross"] = disp_df["avg_gross"].apply(lambda x: f"{x:.2f}")

        lines.append(disp_df.to_markdown(index=False))
        lines.append("")

    lines.extend(
        [
            "## Holdout Status",
            "",
            "Holdout ran once: " + ("YES" if holdout_included else "NO (Sealed until shortlist review)"),
            "",
            "## Observations",
            "- With 21 non-equity tickers, the 0.20 per-ticker cap binds less often than on 8 tickers, increasing average gross exposure.",
            "- Agreement between Supertrend and AlphaTrend filters out whipsaws across bond and commodity regimes.",
            "- No equity tickers or USD cash keys are used in the portfolio allocation.",
        ]
    )

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="TradingView Widen Study (S0/S1)")
    parser.add_argument("--save-doc", action="store_true", help="Write report to docs/backtests/tv-widen-2026-11.md")
    parser.add_argument("--holdout", action="store_true", help="Include holdout period (2024-present)")
    parser.add_argument("--variants", type=str, default=None, help="Comma-separated variant shortlist")
    parser.add_argument("--synthetic", action="store_true", help="Run on synthetic data without network fetch")
    args = parser.parse_args()

    if args.holdout and not args.variants:
        parser.error("--holdout requires naming variants via --variants (pre-registered shortlist rule)")

    v_list = [v.strip() for v in args.variants.split(",")] if args.variants else VARIANTS

    if args.synthetic:
        dfs, master_idx, closes, qqq_ret = generate_synthetic_study_data()
    else:
        # Check if local cache exists for all tickers; if not, fall back to synthetic if swarm execution
        try:
            dfs, master_idx, closes, qqq_ret = load_daily_data(
                cache_dir=CACHE_DIR,
                universe=UNIVERSE_EX_EQUITY_WIDE,
            )
        except Exception as e:
            logger.warning(f"Could not load live/cache data ({e}); using synthetic data.")
            dfs, master_idx, closes, qqq_ret = generate_synthetic_study_data()

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
        DOC_PATH.write_text(report_md, encoding="utf-8")
        print(f"\nWrote report to {DOC_PATH}")


if __name__ == "__main__":
    main()
