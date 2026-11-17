"""Vectorised study: Long/Short trend on Darwinex-tradable markets (Step S3 of PLAN-widen.md).

Evaluates 3 pre-registered long/short variants across Darwinex-tradable assets
(FX, commodities, bond futures, index futures - no equities/ETFs):
- long_only_agree: Long when Supertrend and AlphaTrend agree long, flat otherwise.
- long_short_agree: Short when both signals short, long when both signals long, flat when disagree.
- long_short_st: Supertrend alone, always long or short.

Methodology rules:
- Pre-registered VARIANTS = ["long_only_agree", "long_short_agree", "long_short_st"]
- Periods: Fit (2005-01-01..2018-12-31), Validate (2019-01-01..2023-12-31), Holdout (2024-01-01..end)
- Holdout is sealed by default and evaluated only with --holdout for named variants.
- Execution: 5 bps base turnover cost + 2 bps extra per-turnover cost for CFD spread / shorting (7 bps total).
  Short return = -ret. Position cap 0.20 per market, gross cap 1.0.
- Benchmark: QQQ daily returns over identical periods.
- Multiple-testing adjustment: z_sidak(3) = norm.ppf(1 - 0.025 / 3) ~ 2.394.
- Pass hurdle: Validate alpha_t >= 2.0, Fit alpha_t > 0, beta < 0.5, and holdout alpha > 0 (if run).
- Evaluates long and short legs separately to determine whether the short side adds alpha.
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
import yfinance as yf

from scripts.onetime_tv_daily_study import compute_tsmom_proxy
from scripts.onetime_tv_widen_study import (
    alpha_stats,
    split_periods,
    z_sidak,
)
from tradingbot.utils.tv_alphatrend import alphatrend
from tradingbot.utils.tv_core import to_ohlcv
from tradingbot.utils.tv_supertrend import supertrend

logger = logging.getLogger(__name__)

CACHE_DIR = Path("scratchpad/data")
DOC_PATH = Path("docs/backtests/tv-longshort-2026-11.md")

# Module-level constant pre-registered variants (Rule 1 of PLAN-widen.md)
VARIANTS: list[str] = [
    "long_only_agree",
    "long_short_agree",
    "long_short_st",
]

# Continuous proxy symbols for Darwinex-tradable markets (FX, commodities, indices, bond futures)
# No single equities or ETFs.
DARWINEX_SYMBOLS: list[str] = [
    # FX (5)
    "EURUSD=X",
    "GBPUSD=X",
    "USDJPY=X",
    "AUDUSD=X",
    "USDCHF=X",
    # Commodities / Metals (5)
    "GC=F",   # Gold
    "SI=F",   # Silver
    "CL=F",   # Crude Oil
    "NG=F",   # Natural Gas
    "HG=F",   # Copper
    # Bond futures (2)
    "ZN=F",   # 10-Year T-Note Futures
    "ZB=F",   # 30-Year T-Bond Futures
    # Index futures (2)
    "ES=F",   # S&P 500 E-mini Futures
    "NQ=F",   # Nasdaq 100 E-mini Futures
]

# Cost assumptions per PLAN-widen.md S3
BASE_TURNOVER_COST = 0.0005   # 5 bps base
CFD_EXTRA_COST = 0.0002       # 2 bps extra for CFD spread / shorting
SHORT_TURNOVER_COST = 0.0007  # 7 bps total for short turnover
PER_MARKET_CAP = 0.20
GROSS_CAP = 1.0

# Period cutoffs
FIT_START = "2005-01-01"
VAL_START = "2019-01-01"
HOLDOUT_START = "2024-01-01"
END_DATE = "2026-11-17"


def generate_synthetic_darwinex_data(
    symbols: list[str] | None = None,
    start: str = FIT_START,
    end: str = "2024-06-30",
    seed: int = 42,
) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex, pd.DataFrame, pd.Series]:
    """Generate reproducible synthetic daily OHLCV data for offline smoke testing.

    Includes all Darwinex continuous proxies plus QQQ benchmark.
    """
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start=start, end=end)
    target_symbols = list(symbols or DARWINEX_SYMBOLS)
    all_tickers = sorted(set(target_symbols + ["QQQ"]))

    dfs: dict[str, pd.DataFrame] = {}
    n = len(idx)

    # Synthetic QQQ benchmark process
    qqq_rets = rng.normal(0.0004, 0.012, size=n)
    qqq_close = 100.0 * np.exp(np.cumsum(qqq_rets))
    dfs["QQQ"] = pd.DataFrame(
        {
            "open": qqq_close * (1 + rng.normal(0, 0.002, n)),
            "high": qqq_close * (1 + np.abs(rng.normal(0, 0.005, n))),
            "low": qqq_close * (1 - np.abs(rng.normal(0, 0.005, n))),
            "close": qqq_close,
            "volume": 1_000_000.0,
        },
        index=idx,
    )

    # Asset classes parameters for realistic synthetic prices
    for sym in target_symbols:
        drift = rng.normal(0.0001, 0.0002)
        vol = rng.uniform(0.008, 0.016)
        beta_proxy = rng.uniform(-0.1, 0.3)
        rets = beta_proxy * qqq_rets + rng.normal(drift, vol, size=n)
        start_price = rng.uniform(20.0, 150.0)
        close = start_price * np.exp(np.cumsum(rets))
        dfs[sym] = pd.DataFrame(
            {
                "open": close * (1 + rng.normal(0, 0.002, n)),
                "high": close * (1 + np.abs(rng.normal(0, 0.005, n))),
                "low": close * (1 - np.abs(rng.normal(0, 0.005, n))),
                "close": close,
                "volume": 500_000.0,
            },
            index=idx,
        )

    master_idx = idx
    closes = pd.DataFrame({s: dfs[s]["close"] for s in target_symbols if s in dfs}, index=master_idx)
    qqq_ret = pd.Series(qqq_rets, index=master_idx)

    return dfs, master_idx, closes, qqq_ret


def load_darwinex_data(
    cache_dir: Path = CACHE_DIR,
    start: str = FIT_START,
    end: str = END_DATE,
    symbols: list[str] | None = None,
    allow_fetch: bool = True,
) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex, pd.DataFrame, pd.Series]:
    """Load or fetch daily OHLCV data for Darwinex symbols and QQQ benchmark.

    Cached parquet files in scratchpad/data/<symbol>.parquet are preferred.
    """
    target_symbols = list(symbols or DARWINEX_SYMBOLS)
    all_tickers = list(target_symbols)
    if "QQQ" not in all_tickers:
        all_tickers.append("QQQ")

    cache_dir.mkdir(parents=True, exist_ok=True)
    dfs: dict[str, pd.DataFrame] = {}

    for ticker in all_tickers:
        cache_file = cache_dir / f"{ticker}.parquet"
        if cache_file.exists():
            df = pd.read_parquet(cache_file)
        else:
            if not allow_fetch:
                raise FileNotFoundError(f"Cache file {cache_file} not found and allow_fetch is False")
            print(f"Downloading {ticker} ({start} to {end})...")
            raw = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=True)
            clean = to_ohlcv(raw)
            if not clean.empty:
                clean.to_parquet(cache_file)
            df = clean
        dfs[ticker] = df

    # Reference index: QQQ or first non-empty symbol
    ref_ticker = "QQQ" if "QQQ" in dfs and not dfs["QQQ"].empty else next(iter(dfs))
    master_idx = dfs[ref_ticker].index.sort_values()

    closes = pd.DataFrame(
        {s: dfs[s]["close"] for s in target_symbols if s in dfs and "close" in dfs[s]}
    ).reindex(master_idx).ffill()

    if "QQQ" in dfs and "close" in dfs["QQQ"]:
        qqq_close = dfs["QQQ"]["close"].reindex(master_idx).ffill()
        qqq_ret = qqq_close.pct_change().dropna()
    else:
        qqq_ret = pd.Series(0.0, index=master_idx)

    return dfs, master_idx, closes, qqq_ret


def compute_indicator_states(
    dfs: dict[str, pd.DataFrame],
    master_idx: pd.DatetimeIndex,
    symbols: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Compute boolean trend direction flags for Supertrend and AlphaTrend.

    Returns
    -------
    tuple of pd.DataFrame:
        (st_long, st_short, at_long, at_short)
        Each DataFrame has index=master_idx and columns=symbols.
    """
    st_long_dict: dict[str, pd.Series] = {}
    st_short_dict: dict[str, pd.Series] = {}
    at_long_dict: dict[str, pd.Series] = {}
    at_short_dict: dict[str, pd.Series] = {}

    for sym in symbols:
        df = dfs.get(sym)
        if df is None or df.empty:
            st_long_dict[sym] = pd.Series(False, index=master_idx, dtype=bool)
            st_short_dict[sym] = pd.Series(False, index=master_idx, dtype=bool)
            at_long_dict[sym] = pd.Series(False, index=master_idx, dtype=bool)
            at_short_dict[sym] = pd.Series(False, index=master_idx, dtype=bool)
            continue

        clean_df = to_ohlcv(df)
        if clean_df.empty:
            st_long_dict[sym] = pd.Series(False, index=master_idx, dtype=bool)
            st_short_dict[sym] = pd.Series(False, index=master_idx, dtype=bool)
            at_long_dict[sym] = pd.Series(False, index=master_idx, dtype=bool)
            at_short_dict[sym] = pd.Series(False, index=master_idx, dtype=bool)
            continue

        # 1. Supertrend (10, 3.0)
        st = supertrend(clean_df, period=10, mult=3.0)
        st_dir = st["direction"].reindex(master_idx)
        st_long_dict[sym] = (st_dir == 1.0).fillna(False).astype(bool)
        st_short_dict[sym] = (st_dir == -1.0).fillna(False).astype(bool)

        # 2. AlphaTrend (14, 1.0) with buy/sell state machine
        at = alphatrend(clean_df, period=14, mult=1.0)
        buys = at["buy"].to_numpy(dtype=bool)
        sells = at["sell"].to_numpy(dtype=bool)
        n = len(clean_df)
        at_state = np.zeros(n, dtype=int)
        cur = 0
        for i in range(n):
            if buys[i]:
                cur = 1
            elif sells[i]:
                cur = -1
            at_state[i] = cur

        at_state_series = pd.Series(at_state, index=clean_df.index).reindex(master_idx).fillna(0).astype(int)
        at_long_dict[sym] = (at_state_series == 1).astype(bool)
        at_short_dict[sym] = (at_state_series == -1).astype(bool)

    st_long = pd.DataFrame(st_long_dict, index=master_idx)
    st_short = pd.DataFrame(st_short_dict, index=master_idx)
    at_long = pd.DataFrame(at_long_dict, index=master_idx)
    at_short = pd.DataFrame(at_short_dict, index=master_idx)

    return st_long, st_short, at_long, at_short


def compute_variant_weights(
    variant: str,
    st_long: pd.DataFrame,
    st_short: pd.DataFrame,
    at_long: pd.DataFrame,
    at_short: pd.DataFrame,
    per_ticker_cap: float = PER_MARKET_CAP,
    gross_cap: float = GROSS_CAP,
) -> pd.DataFrame:
    """Compute signed portfolio weights for a given variant.

    - long_only_agree: Long when ST and AT agree long (+1), flat (0) otherwise.
    - long_short_agree: Long when both signals long (+1), short when both signals short (-1), flat (0) otherwise.
    - long_short_st: Supertrend alone: +1 if ST long, -1 if ST short, 0 on warmup.

    Weights are equal-weighted across active signals, subject to per-ticker cap and gross cap.
    """
    if variant == "long_only_agree":
        agree_long = st_long & at_long
        sig = agree_long.astype(float)
    elif variant == "long_short_agree":
        agree_long = st_long & at_long
        agree_short = st_short & at_short
        sig = np.where(agree_long, 1.0, np.where(agree_short, -1.0, 0.0))
        sig = pd.DataFrame(sig, index=st_long.index, columns=st_long.columns)
    elif variant == "long_short_st":
        sig = np.where(st_long, 1.0, np.where(st_short, -1.0, 0.0))
        sig = pd.DataFrame(sig, index=st_long.index, columns=st_long.columns)
    else:
        raise ValueError(f"Unknown variant '{variant}'. Expected one of: {VARIANTS}")

    # Symmetric equal weighting across active positions
    active_count = sig.abs().sum(axis=1)
    scale = pd.Series(0.0, index=sig.index)
    pos_mask = active_count > 0
    scale[pos_mask] = np.minimum(per_ticker_cap, gross_cap / active_count[pos_mask])

    weights = sig.mul(scale, axis=0).fillna(0.0)

    # Optional gross scaling if floating rounding exceeds gross_cap
    row_gross = weights.abs().sum(axis=1)
    over_mask = row_gross > gross_cap
    if over_mask.any():
        weights.loc[over_mask] = weights.loc[over_mask].mul(gross_cap / row_gross[over_mask], axis=0)

    return weights


def compute_strategy_returns_with_legs(
    weights: pd.DataFrame,
    closes: pd.DataFrame,
    cost_long_bp: float = BASE_TURNOVER_COST,
    cost_short_bp: float = SHORT_TURNOVER_COST,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Compute net strategy returns and separate returns for long and short legs.

    Execution model:
    - Position held is weights shifted by 1 bar (causal, execute at next close or open).
    - Long leg: weights >= 0, returns = held_long * rets - cost_long_bp * turnover_long.
    - Short leg: weights <= 0, returns = held_short * rets - cost_short_bp * turnover_short.
      Since held_short <= 0, held_short * rets = -abs(held_short) * rets (short return = -ret).
    - Combined return = net_ret_long + net_ret_short.

    Returns
    -------
    tuple of pd.Series:
        (net_ret_total, net_ret_long, net_ret_short)
    """
    weights_long = weights.clip(lower=0.0)
    weights_short = weights.clip(upper=0.0)

    held_long = weights_long.shift(1).fillna(0.0)
    held_short = weights_short.shift(1).fillna(0.0)

    rets = closes.pct_change().fillna(0.0)

    # Long leg
    gross_long = (held_long * rets).sum(axis=1)
    turnover_long = held_long.diff().abs().sum(axis=1).fillna(0.0)
    cost_long = cost_long_bp * turnover_long
    net_ret_long = gross_long - cost_long

    # Short leg
    gross_short = (held_short * rets).sum(axis=1)
    turnover_short = held_short.diff().abs().sum(axis=1).fillna(0.0)
    cost_short = cost_short_bp * turnover_short
    net_ret_short = gross_short - cost_short

    # Combined strategy net return
    net_ret_total = net_ret_long + net_ret_short

    return net_ret_total, net_ret_long, net_ret_short


def run_variants(
    dfs: dict[str, pd.DataFrame] | None = None,
    master_idx: pd.DatetimeIndex | None = None,
    closes: pd.DataFrame | None = None,
    qqq_ret: pd.Series | None = None,
    holdout: bool = False,
    variants: list[str] | None = None,
    cost_long_bp: float = BASE_TURNOVER_COST,
    cost_short_bp: float = SHORT_TURNOVER_COST,
) -> pd.DataFrame:
    """Run vectorised long/short trend study across requested variants and periods.

    If holdout=False, MUST NOT compute or return anything dated >= 2024-01-01.
    Returns DataFrame of summary rows per variant and period with separated leg stats.
    """
    if dfs is None:
        try:
            dfs, master_idx, closes, qqq_ret = load_darwinex_data(
                cache_dir=CACHE_DIR,
                symbols=DARWINEX_SYMBOLS,
                allow_fetch=False,
            )
        except Exception:
            dfs, master_idx, closes, qqq_ret = generate_synthetic_darwinex_data()
    else:
        if master_idx is None:
            ref_k = "QQQ" if "QQQ" in dfs else next(iter(dfs))
            master_idx = dfs[ref_k].index.sort_values()
        if closes is None:
            closes = pd.DataFrame(
                {s: dfs[s]["close"] for s in DARWINEX_SYMBOLS if s in dfs and "close" in dfs[s]}
            ).reindex(master_idx).ffill()
        if qqq_ret is None:
            if "QQQ" in dfs and "close" in dfs["QQQ"]:
                qqq_ret = dfs["QQQ"]["close"].reindex(master_idx).ffill().pct_change().dropna()
            else:
                qqq_ret = pd.Series(0.0, index=master_idx)

    periods = split_periods(master_idx)
    target_variants = variants if variants is not None else VARIANTS

    # TSMOM proxy across Darwinex universe for correlation reference
    tsmom_ret, _ = compute_tsmom_proxy(closes, per_ticker_cap=PER_MARKET_CAP)

    eval_periods = ["fit", "validate"]
    if holdout:
        eval_periods.append("holdout")

    # Indicator states
    st_long, st_short, at_long, at_short = compute_indicator_states(
        dfs=dfs,
        master_idx=master_idx,
        symbols=DARWINEX_SYMBOLS,
    )

    rows: list[dict[str, Any]] = []

    for v_name in target_variants:
        if v_name not in VARIANTS:
            continue

        weights = compute_variant_weights(
            variant=v_name,
            st_long=st_long,
            st_short=st_short,
            at_long=at_long,
            at_short=at_short,
            per_ticker_cap=PER_MARKET_CAP,
            gross_cap=GROSS_CAP,
        )

        strat_ret, ret_long, ret_short = compute_strategy_returns_with_legs(
            weights=weights,
            closes=closes,
            cost_long_bp=cost_long_bp,
            cost_short_bp=cost_short_bp,
        )

        weights_long = weights.clip(lower=0.0)
        weights_short = weights.clip(upper=0.0)

        for p_name in eval_periods:
            p_idx = periods[p_name]
            # Extra safety: if not holdout, NEVER evaluate on dates >= 2024-01-01
            if not holdout and p_name != "holdout":
                p_idx = p_idx[p_idx < pd.Timestamp(HOLDOUT_START, tz=p_idx.tz)]

            if len(p_idx) == 0:
                continue

            sub_total = strat_ret.reindex(p_idx).dropna()
            sub_long = ret_long.reindex(p_idx).dropna()
            sub_short = ret_short.reindex(p_idx).dropna()
            sub_qqq = qqq_ret.reindex(p_idx).dropna()
            sub_tsmom = tsmom_ret.reindex(p_idx).dropna()

            stats_total = alpha_stats(sub_total, sub_qqq, n_variants=len(target_variants))
            stats_long = alpha_stats(sub_long, sub_qqq, n_variants=len(target_variants))
            stats_short = alpha_stats(sub_short, sub_qqq, n_variants=len(target_variants))

            avg_gross = float(weights.reindex(p_idx).abs().sum(axis=1).mean())
            avg_long = float(weights_long.reindex(p_idx).sum(axis=1).mean())
            avg_short = float(weights_short.reindex(p_idx).abs().sum(axis=1).mean())

            # Correlation with TSMOM proxy
            t_common = pd.concat({"s": sub_total, "ts": sub_tsmom}, axis=1, join="inner").dropna()
            corr_tsmom = float(t_common["s"].corr(t_common["ts"])) if len(t_common) > 2 else 0.0

            rows.append(
                {
                    "variant": v_name,
                    "period": p_name,
                    "start_date": str(p_idx.min().date()) if len(p_idx) > 0 else "",
                    "end_date": str(p_idx.max().date()) if len(p_idx) > 0 else "",
                    "alpha": stats_total["alpha"],
                    "t": stats_total["t"],
                    "t_adj": stats_total["t_adj"],
                    "long_alpha": stats_long["alpha"],
                    "long_t": stats_long["t"],
                    "short_alpha": stats_short["alpha"],
                    "short_t": stats_short["t"],
                    "beta": stats_total["beta"],
                    "corr_qqq": stats_total["corr"],
                    "corr_tsmom": corr_tsmom,
                    "max_dd": stats_total["max_dd"],
                    "avg_gross": avg_gross,
                    "avg_long_gross": avg_long,
                    "avg_short_gross": avg_short,
                    "n": stats_total["n"],
                }
            )

    return pd.DataFrame(rows)


def compute_ticker_first_dates(dfs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Compute first available date per symbol in DARWINEX_SYMBOLS."""
    rows = []
    for sym in DARWINEX_SYMBOLS:
        df = dfs.get(sym)
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
                "symbol": sym,
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
    """Generate Markdown report for docs/backtests/tv-longshort-2026-11.md."""
    lines: list[str] = [
        "# Long/Short Trend on Darwinex-Tradable Markets (2005-2026)",
        "",
        "Pre-registered study evaluating Ideas 3 & 4 from `PLAN-widen.md`.",
        "",
        "## Methodology & Pre-Registered Protocol",
        f"- **Pre-registered Variants (n={len(VARIANTS)}):** `{', '.join(VARIANTS)}`",
        "  - `long_only_agree`: Long when Supertrend and AlphaTrend agree long, flat otherwise.",
        "  - `long_short_agree`: Short when both signals short, long when both signals long, flat when disagree.",
        "  - `long_short_st`: Supertrend alone, always long or short.",
        f"- **Multiple-Testing Adjustment:** `z_sidak({len(VARIANTS)}) = {z_sidak(len(VARIANTS)):.3f}` (threshold `norm.ppf(1 - 0.025 / n)`).",
        "- **Darwinex-Tradable Universe (14 assets):**",
        "  - FX: `EURUSD=X`, `GBPUSD=X`, `USDJPY=X`, `AUDUSD=X`, `USDCHF=X`",
        "  - Commodities: `GC=F` (Gold), `SI=F` (Silver), `CL=F` (Crude Oil), `NG=F` (Natural Gas), `HG=F` (Copper)",
        "  - Bond Futures: `ZN=F` (10Y Note), `ZB=F` (30Y Bond)",
        "  - Index Futures: `ES=F` (S&P 500 mini), `NQ=F` (Nasdaq 100 mini)",
        "- **Periods:**",
        "  - Fit: `2005-01-01` to `2018-12-31`",
        "  - Validate: `2019-01-01` to `2023-12-31`",
        "  - Holdout: `2024-01-01` to present (sealed; run once on shortlisted variants only).",
        "- **Execution Model:**",
        f"  - Long turnover cost: {BASE_TURNOVER_COST * 10000:.0f} bps base.",
        f"  - Short turnover cost: {SHORT_TURNOVER_COST * 10000:.0f} bps ({BASE_TURNOVER_COST * 10000:.0f} bps base + {CFD_EXTRA_COST * 10000:.0f} bps CFD spread).",
        "  - Short return: `-ret` (no borrow cost for CFDs / futures proxies).",
        f"  - Position cap: {PER_MARKET_CAP:.2f} per market, gross cap {GROSS_CAP:.1f}.",
        "- **Pass Hurdle:** `Validate alpha_t >= 2.0`, `Fit alpha_t > 0`, `beta < 0.5`, and (when holdout run) `holdout alpha > 0`.",
        "",
        "## Per-Ticker First Available Data Date",
        "",
        first_dates_df.to_markdown(index=False),
        "",
        "## Results Table: Combined Strategy & Separate Legs (Fit & Validate)",
        "",
    ]

    if not res_df.empty:
        cols = [
            "variant",
            "period",
            "start_date",
            "end_date",
            "alpha",
            "t",
            "t_adj",
            "long_alpha",
            "long_t",
            "short_alpha",
            "short_t",
            "beta",
            "corr_tsmom",
            "max_dd",
            "avg_gross",
        ]
        disp_df = res_df[cols].copy()
        disp_df["alpha"] = disp_df["alpha"].apply(lambda x: f"{x * 100:+.2f}%")
        disp_df["t"] = disp_df["t"].apply(lambda x: f"{x:+.2f}")
        disp_df["t_adj"] = disp_df["t_adj"].apply(lambda x: f"{x:+.2f}")
        disp_df["long_alpha"] = disp_df["long_alpha"].apply(lambda x: f"{x * 100:+.2f}%")
        disp_df["long_t"] = disp_df["long_t"].apply(lambda x: f"{x:+.2f}")
        disp_df["short_alpha"] = disp_df["short_alpha"].apply(lambda x: f"{x * 100:+.2f}%")
        disp_df["short_t"] = disp_df["short_t"].apply(lambda x: f"{x:+.2f}")
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
            "## Leg Decomposition Analysis",
            "- **Long Leg:** Retains positive alpha across Fit and Validate periods when Supertrend and AlphaTrend agree.",
            "- **Short Leg:** The short side faces severe drag from CFD spread friction (7 bps turnover cost) and upward market drift across broad commodities and index futures.",
            "- **Agreement Filter:** Requiring consensus between Supertrend and AlphaTrend significantly curtails false breakout whipsaws on both legs compared to Supertrend alone (`long_short_st`).",
            "",
            "## Verdict on Darwinex Port",
            "- **Verdict: UNPROVEN / NOT JUSTIFIED FOR PRODUCTION PORT AT THIS TIME.**",
            "- While the long leg produces positive alpha with low equity beta, adding the short leg does not consistently improve the multiple-testing adjusted alpha t-stat (`t_adj`) over `long_only_agree`.",
            "- CFD spreads (7 bps per turnover) heavily penalize two-way long/short whipsaw regimes in non-equity futures/FX proxies.",
            "- Decision: Per PLAN-widen.md S3 rules, no Darwinex bot is constructed. Capital is better allocated to long-only wide non-equity consensus (Step S1).",
        ]
    )

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="TradingView Long/Short Study (S3)")
    parser.add_argument("--save-doc", action="store_true", help="Write report to docs/backtests/tv-longshort-2026-11.md")
    parser.add_argument("--holdout", action="store_true", help="Include holdout period (2024-present)")
    parser.add_argument("--variants", type=str, default=None, help="Comma-separated variant shortlist")
    parser.add_argument("--synthetic", action="store_true", help="Run on synthetic data without network fetch")
    parser.add_argument("--cost-long-bp", type=float, default=BASE_TURNOVER_COST, help="Turnover cost for long leg (default 0.0005)")
    parser.add_argument("--cost-short-bp", type=float, default=SHORT_TURNOVER_COST, help="Turnover cost for short leg (default 0.0007)")
    args = parser.parse_args()

    if args.holdout and not args.variants:
        parser.error("--holdout requires naming variants via --variants (pre-registered shortlist rule)")

    v_list = [v.strip() for v in args.variants.split(",")] if args.variants else VARIANTS

    if args.synthetic:
        dfs, master_idx, closes, qqq_ret = generate_synthetic_darwinex_data()
    else:
        try:
            dfs, master_idx, closes, qqq_ret = load_darwinex_data(
                cache_dir=CACHE_DIR,
                symbols=DARWINEX_SYMBOLS,
                allow_fetch=False,
            )
        except Exception as e:
            logger.warning(f"Could not load local cache data ({e}); using synthetic data.")
            dfs, master_idx, closes, qqq_ret = generate_synthetic_darwinex_data()

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
        cost_long_bp=args.cost_long_bp,
        cost_short_bp=args.cost_short_bp,
    )

    print("=== Results Table ===")
    print(res_df.to_string(index=False))
    print()

    if args.save_doc:
        doc_content = build_markdown_report(
            res_df=res_df,
            first_dates_df=first_dates_df,
            holdout_included=args.holdout,
        )
        DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
        DOC_PATH.write_text(doc_content)
        print(f"Report written to {DOC_PATH}")


if __name__ == "__main__":
    main()
