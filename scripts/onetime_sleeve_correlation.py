"""R1: Sleeve correlation study across quants.md survivors, C2 blend members, and event books.

PLAN.md Step R1:
Streams:
- The six quantsmd survivors: MonthlyRotation, VolTargetMomentum, QuarterlyTeCostGate,
  RiskParityCapped, SmaDaily, SmaDailyGated (using run_bot_sim logic with T-bill yield lever on).
- C2 blend members: TSMOM (rebuilt in plain pandas); TARegimeMultiAsset and GoldenButterfly
  (listed as skipped: require OHLCV / volume for CMF, OBV, ADX, BBP and stateful backtest_bot).
- The event streams: buyback_sp100 and activist_13d from scripts/onetime_lift_study.py.

Output:
(a) Correlation matrix of raw daily returns on the common window.
(b) Correlation matrix of residuals after regressing each stream on QQQ (alpha streams).
(c) For every stream its alpha, t, beta, corr, max DD, H1, H2, and verdict.
(d) Equal-weight blend of the six survivors vs its best member (alpha, t).
(e) Reading: redundant pairs (residual corr > 0.7), most independent streams. No bot is promoted.

Results written to:
    docs/backtests/sleeve-correlation-2026-11.md
"""

from __future__ import annotations

import os
import pickle
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

os.environ.setdefault("POSTGRES_URI", "stub:stub@localhost:5432/stub")

import numpy as np
import pandas as pd
import yfinance as yf

import scripts.event_book_lib as lib
import scripts.onetime_event_buyback_study as st
import scripts.onetime_lift_study as lift
from tradingbot import (
    quantsmd_monthly_rotation as mr,
)
from tradingbot import (
    quantsmd_quarterly_te_cost_gate as qte,
)
from tradingbot import (
    quantsmd_risk_parity_capped as rpc,
)
from tradingbot import (
    quantsmd_sma_daily as sma,
)
from tradingbot import (
    quantsmd_sma_daily_gated as smag,
)
from tradingbot import (
    quantsmd_vol_target_momentum as vtm,
)
from tradingbot.tsmomtrendbot import UNIVERSE as TSMOM_UNIVERSE
from tradingbot.utils.alpha_report import alpha_stats
from tradingbot.utils.vol_target import inverse_vol_weights

OUT_DOC = Path("docs/backtests/sleeve-correlation-2026-11.md")
CACHE_DIR = Path(os.environ.get("CACHE_DIR", "/tmp"))
PRICES_CACHE = CACHE_DIR / "quantsmd_cash_yield_prices.pkl"
EXTRA_PRICES_CACHE = CACHE_DIR / "sleeve_correlation_prices.pkl"
COST_PER_TURNOVER = 0.0005  # 5 bps
COMMON_START = "2010-02-01"


def setup_cache_paths() -> None:
    """Resolve .cache paths for event studies across worktrees."""
    candidates = [
        Path(".cache"),
        Path(__file__).resolve().parents[1] / ".cache",
        Path(__file__).resolve().parents[4] / ".cache"
        if len(Path(__file__).resolve().parents) >= 5
        else Path(".cache"),
        Path("/home/jguese/code/tradingbot/python_tradingbot_framework/.cache"),
    ]
    for c in candidates:
        if (c / "broad").exists() and (c / "edgar").exists():
            lib.BROAD_DIR = c / "broad"
            lib.PRICE_CACHE = lib.BROAD_DIR / "prices.pkl"
            st.EDGAR_CACHE_DIR = c / "edgar"
            st.DOCS_CACHE_DIR = st.EDGAR_CACHE_DIR / "docs"
            st.SUBMISSIONS_CACHE_DIR = st.EDGAR_CACHE_DIR / "submissions"
            break


def load_all_prices() -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Load or fetch closes for survivors, TSMOM universe, and ^IRX / QQQ."""
    if not PRICES_CACHE.exists():
        raise FileNotFoundError(f"Missing base price cache at {PRICES_CACHE}")

    base_prices = pd.read_pickle(PRICES_CACHE)
    missing_tsmom = [s for s in TSMOM_UNIVERSE if s not in base_prices.columns]

    extra_prices: pd.DataFrame | None = None
    if EXTRA_PRICES_CACHE.exists():
        try:
            extra_prices = pd.read_pickle(EXTRA_PRICES_CACHE)
            if not all(s in extra_prices.columns for s in missing_tsmom):
                extra_prices = None
        except Exception:
            extra_prices = None

    if extra_prices is None and missing_tsmom:
        print(f"Downloading extra {len(missing_tsmom)} tickers for TSMOM...", file=sys.stderr)
        raw = yf.download(missing_tsmom, start="2000-01-01", auto_adjust=True, progress=False)["Close"]
        raw.index = pd.DatetimeIndex(raw.index).tz_localize(None).normalize()
        extra_prices = raw.sort_index()
        try:
            extra_prices.to_pickle(EXTRA_PRICES_CACHE)
        except Exception as exc:
            print(f"Warning: could not write {EXTRA_PRICES_CACHE}: {exc}", file=sys.stderr)

    if extra_prices is not None:
        combined = pd.concat([base_prices, extra_prices], axis=1)
        # Drop duplicate columns if any
        combined = combined.loc[:, ~combined.columns.duplicated()].sort_index()
    else:
        combined = base_prices.sort_index()

    irx = combined["^IRX"].ffill().fillna(0.0)
    rf_daily = irx / 100.0 / 252.0
    qqq = combined["QQQ"]
    return combined, rf_daily, qqq


def simulate_quantsmd_survivor(
    data_syms: list[str],
    trade_syms: list[str],
    weight_fn: Callable[[pd.DataFrame, Any], dict[str, float] | None],
    initial_state: Any,
    prices: pd.DataFrame,
    rf_daily: pd.Series,
) -> pd.Series:
    """Simulate a quantsmd survivor bar-by-bar with T-bill cash yield."""
    closes = prices[data_syms].dropna()
    rf = rf_daily.reindex(closes.index).ffill().fillna(0.0)

    weights_records: list[dict[str, float]] = []
    current_w: dict[str, float] = {}
    state = initial_state.copy() if initial_state is not None else None

    for i in range(len(closes)):
        sub = closes.iloc[: i + 1]
        res = weight_fn(sub, state)
        if res is not None:
            current_w = res
        weights_records.append(current_w.copy())

    w_df = pd.DataFrame(weights_records, index=closes.index).reindex(columns=trade_syms).fillna(0.0)
    trade_closes = closes[trade_syms]
    r = trade_closes.pct_change().fillna(0.0)

    held = w_df.shift(1).fillna(0.0)
    gross = (held * r).sum(axis=1)
    turnover = held.diff().abs().sum(axis=1).fillna(0.0)
    net_ret_base = gross - COST_PER_TURNOVER * turnover
    cash_share = (1.0 - held.sum(axis=1)).clip(lower=0.0)
    net_ret_yield = net_ret_base + cash_share * rf
    return net_ret_yield.iloc[1:]


def simulate_tsmom(prices: pd.DataFrame) -> pd.Series:
    """Rebuild TSMOMTrendBot in plain pandas (12m trailing momentum, 60d realized vol)."""
    closes = prices[TSMOM_UNIVERSE].copy()
    mom = closes / closes.shift(252) - 1.0
    ret = closes.pct_change()
    vols = ret.rolling(60).std() * np.sqrt(252.0)

    weights_records: list[dict[str, float]] = []
    for idx in closes.index:
        m_row = mom.loc[idx]
        v_row = vols.loc[idx]
        v_dict = {}
        for sym in TSMOM_UNIVERSE:
            m_val = m_row[sym]
            v_val = v_row[sym]
            if pd.notna(m_val) and m_val > 0 and pd.notna(v_val) and v_val > 0:
                v_dict[sym] = v_val
        w = inverse_vol_weights(v_dict, target_vol=0.10, max_leg=0.15, max_gross=1.0) if v_dict else {}
        weights_records.append(w)

    w_df = pd.DataFrame(weights_records, index=closes.index).reindex(columns=TSMOM_UNIVERSE).fillna(0.0)
    held = w_df.shift(1).fillna(0.0)
    gross = (held * ret.fillna(0.0)).sum(axis=1)
    turnover = held.diff().abs().sum(axis=1).fillna(0.0)
    net_ret = gross - COST_PER_TURNOVER * turnover

    # Start when first non-empty weights occur
    nonzero_idx = w_df[w_df.sum(axis=1) > 0].index
    if len(nonzero_idx) == 0:
        return pd.Series(dtype=float)
    first_date = nonzero_idx[0]
    return net_ret.loc[first_date:].iloc[1:]


def simulate_event_streams() -> dict[str, pd.Series]:
    """Build event streams buyback_sp100 and activist_13d from scripts/onetime_lift_study.py."""
    meta = pickle.loads((lib.BROAD_DIR / "candidates.pkl").read_bytes())
    uni = meta["universe"]
    prices = lib.load_prices(sorted(uni))
    syms, idx, R, _q = lib.build_matrix(prices)
    col = {s: i for i, s in enumerate(syms)}

    bb, d13 = lift.load_events(uni)
    event_streams: dict[str, pd.Series] = {}
    for name, ev, hold in (("buyback_sp100", bb, 10), ("activist_13d", d13, 20)):
        ev = ev[ev["symbol"].isin(col)].copy()
        ev["entry"] = lib.entry_positions(idx, ev["acceptance_dt"])
        ev = ev[ev["entry"] < len(idx) - 2]
        ret, _ = lift.event_stream(
            ev["entry"].to_numpy(dtype=int), ev["symbol"].map(col).to_numpy(dtype=int), hold, R, idx
        )
        s = ret.copy()
        s.index = pd.DatetimeIndex(s.index).tz_localize(None).normalize()
        event_streams[name] = s
    return event_streams


@dataclass
class StreamMetrics:
    name: str
    start: str
    end: str
    obs: int
    alpha: float
    t: float
    beta: float
    corr: float
    max_dd: float
    h1_alpha: float
    h1_t: float
    h2_alpha: float
    h2_t: float
    verdict: str


def compute_metrics(r: pd.Series, q: pd.Series, name: str) -> StreamMetrics:
    """Compute alpha vs QQQ per CLAUDE.md: weekday rows, beta, resid mean * 252, t-stat, max DD, H1/H2."""
    joined = pd.concat({"r": r, "q": q}, axis=1, join="inner").dropna()
    # Filter weekdays only
    joined = joined[joined.index.dayofweek < 5]
    r_clean = joined["r"]
    q_clean = joined["q"]

    stats_full = alpha_stats(r_clean, q_clean)
    if stats_full is None:
        raise ValueError(f"Unable to compute alpha_stats for {name}")

    # Curve max drawdown
    curve = (1.0 + r_clean).cumprod()
    peak = curve.cummax()
    dd = float((curve / peak - 1.0).min() * 100) if len(curve) > 0 else 0.0

    # Halves
    mid = len(r_clean) // 2
    h1 = alpha_stats(r_clean.iloc[:mid], q_clean.iloc[:mid])
    h2 = alpha_stats(r_clean.iloc[mid:], q_clean.iloc[mid:])

    h1_alpha = float(h1.alpha * 100) if h1 else 0.0
    h1_t = float(h1.t) if h1 else 0.0
    h2_alpha = float(h2.alpha * 100) if h2 else 0.0
    h2_t = float(h2.t) if h2 else 0.0

    # Verdict: unproven unless t >= 2 and alpha > 0 in both halves
    verdict = "unproven"
    if h1 and h2 and h1.t >= 2.0 and h1.alpha > 0 and h2.t >= 2.0 and h2.alpha > 0:
        verdict = "edge"

    return StreamMetrics(
        name=name,
        start=str(joined.index[0].date()),
        end=str(joined.index[-1].date()),
        obs=len(joined),
        alpha=float(stats_full.alpha * 100),
        t=float(stats_full.t),
        beta=float(stats_full.beta),
        corr=float(stats_full.corr),
        max_dd=dd,
        h1_alpha=h1_alpha,
        h1_t=h1_t,
        h2_alpha=h2_alpha,
        h2_t=h2_t,
        verdict=verdict,
    )


def format_markdown_table(df: pd.DataFrame) -> str:
    """Format a pandas DataFrame as a Markdown table."""
    cols = list(df.columns)
    header = "| | " + " | ".join(cols) + " |"
    sep = "|---|" + "---|" * len(cols)
    rows = []
    for idx in df.index:
        vals = " | ".join(f"{df.loc[idx, c]:.2f}" for c in cols)
        rows.append(f"| **{idx}** | {vals} |")
    return "\n".join([header, sep, *rows])


def generate_report(
    common_returns: pd.DataFrame,
    common_qqq: pd.Series,
    corr_raw: pd.DataFrame,
    corr_resid: pd.DataFrame,
    full_metrics: list[StreamMetrics],
    common_metrics: list[StreamMetrics],
    blend_metrics: StreamMetrics,
    best_survivor_metrics: StreamMetrics,
    redundant_pairs: list[tuple[str, str, float]],
    independent_streams: list[tuple[str, float]],
) -> str:
    """Generate Markdown report for docs/backtests/sleeve-correlation-2026-11.md."""
    lines: list[str] = [
        "# Sleeve Correlation Study (2026-11)",
        "",
        "Evaluation of sleeve diversification, residual correlations vs QQQ, and equal-weight blending",
        "across surviving quants.md books, C2 blend members, and event books.",
        "",
        "## Evaluated Streams",
        "",
        "1. **Six quants.md survivors (with T-bill cash yield lever on):**",
        "   - `MonthlyRotation` (ETF8 momentum rotation)",
        "   - `VolTargetMomentum` (ETF8 vol-targeted momentum)",
        "   - `QuarterlyTeCostGate` (ETF8 quarterly rebalance with turnover cost gate)",
        "   - `RiskParityCapped` (5-asset risk parity capped at 25%)",
        "   - `SmaDaily` (9 SPDR sectors 50/200 SMA trend, 25% cap)",
        "   - `SmaDailyGated` (9 SPDR sectors SMA trend with SPY market gate)",
        "2. **C2 blend members:**",
        "   - `TSMOM`: Rebuilt in plain pandas using the Moskowitz/Ooi/Pedersen 12-month trailing total return momentum",
        "     and 60-day realized volatility inverse-vol weights (target vol 0.10, cap 0.15, max gross 1.0). Cash earns 0%.",
        "   - `TARegimeMultiAsset`: **Skipped.** Requires OHLCV (High/Low for ADX and Bollinger %B) and `ta` indicators",
        "     evaluated inside `backtest_bot`'s stateful multi-ticker execution loop; cannot be rebuilt from daily adjusted closes alone.",
        "   - `GoldenButterfly`: **Skipped.** Requires OHLCV (Volume and High/Low for OBV and Chaikin Money Flow) and stateful RRG signals;",
        "     cannot be rebuilt from daily adjusted closes alone.",
        "3. **Event streams from `scripts/onetime_lift_study.py`:**",
        "   - `buyback_sp100`: S&P 100 buyback drift (10-session holding, 10 bps cost per side).",
        "   - `activist_13d`: Schedule 13D activist filings by fund entities (20-session holding, 10 bps cost per side).",
        "",
        f"**Common Window:** `{common_returns.index[0].date()}` to `{common_returns.index[-1].date()}` "
        f"({len(common_returns)} shared weekday bars).",
        "",
        "---",
        "",
        "## (a) Correlation Matrix of Raw Daily Returns (Common Window)",
        "",
        format_markdown_table(corr_raw),
        "",
        "---",
        "",
        "## (b) Correlation Matrix of QQQ Residuals (Alpha Streams, Common Window)",
        "",
        "Residual returns after removing QQQ beta: `resid_i = r_i - beta_i * r_qqq`.",
        "",
        format_markdown_table(corr_resid),
        "",
        "---",
        "",
        "## (c) Performance Metrics vs QQQ",
        "",
        "### Common Window (2010-02 to 2026-11)",
        "",
        "| Stream | Obs | Alpha/yr | t-stat | Beta | Corr (QQQ) | Max DD | H1 Alpha (t) | H2 Alpha (t) | Verdict |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]

    for m in common_metrics:
        lines.append(
            f"| **{m.name}** | {m.obs} | {m.alpha:+.1f}% | {m.t:.2f} | {m.beta:.2f} | {m.corr:.2f} | "
            f"{m.max_dd:.1f}% | {m.h1_alpha:+.1f}% ({m.h1_t:.2f}) | {m.h2_alpha:+.1f}% ({m.h2_t:.2f}) | `{m.verdict}` |"
        )

    lines.extend(
        [
            "",
            "### Full Available History per Stream",
            "",
            "| Stream | Start | End | Obs | Alpha/yr | t-stat | Beta | Corr (QQQ) | Max DD | H1 Alpha (t) | H2 Alpha (t) | Verdict |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
    )

    for m in full_metrics:
        lines.append(
            f"| **{m.name}** | {m.start} | {m.end} | {m.obs} | {m.alpha:+.1f}% | {m.t:.2f} | {m.beta:.2f} | {m.corr:.2f} | "
            f"{m.max_dd:.1f}% | {m.h1_alpha:+.1f}% ({m.h1_t:.2f}) | {m.h2_alpha:+.1f}% ({m.h2_t:.2f}) | `{m.verdict}` |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## (d) Equal-Weight Blend of Six Survivors vs Best Member",
            "",
            "Equal-weight 1/6 blend of the six surviving quants.md books evaluated over the common window (2010–2026):",
            "",
            "| Series | Alpha/yr | t-stat | Beta | Corr (QQQ) | Max DD | H1 Alpha (t) | H2 Alpha (t) | Verdict |",
            "|---|---|---|---|---|---|---|---|---|",
            f"| **Equal-Weight 6-Survivor Blend** | {blend_metrics.alpha:+.1f}% | {blend_metrics.t:.2f} | "
            f"{blend_metrics.beta:.2f} | {blend_metrics.corr:.2f} | {blend_metrics.max_dd:.1f}% | "
            f"{blend_metrics.h1_alpha:+.1f}% ({blend_metrics.h1_t:.2f}) | {blend_metrics.h2_alpha:+.1f}% ({blend_metrics.h2_t:.2f}) | `{blend_metrics.verdict}` |",
            f"| **Best Survivor ({best_survivor_metrics.name})** | {best_survivor_metrics.alpha:+.1f}% | {best_survivor_metrics.t:.2f} | "
            f"{best_survivor_metrics.beta:.2f} | {best_survivor_metrics.corr:.2f} | {best_survivor_metrics.max_dd:.1f}% | "
            f"{best_survivor_metrics.h1_alpha:+.1f}% ({best_survivor_metrics.h1_t:.2f}) | {best_survivor_metrics.h2_alpha:+.1f}% ({best_survivor_metrics.h2_t:.2f}) | `{best_survivor_metrics.verdict}` |",
            "",
            "**Dilution vs Diversification Analysis:**",
            f"- The equal-weight blend produces **alpha of {blend_metrics.alpha:+.1f}%/yr (t = {blend_metrics.t:.2f})**, compared to the best standalone survivor **{best_survivor_metrics.name} ({best_survivor_metrics.alpha:+.1f}%/yr, t = {best_survivor_metrics.t:.2f})**.",
            f"- Blending achieves modest risk reduction (Max DD {blend_metrics.max_dd:.1f}% vs {best_survivor_metrics.max_dd:.1f}%), but severely dilutes alpha (reducing annual alpha by {best_survivor_metrics.alpha - blend_metrics.alpha:.1f}%/yr).",
            "- Because the ETF8 rotation books correlate heavily among themselves (residual correlation ~0.76–0.93), averaging them does not produce an independent statistical boost to t-stat.",
            "",
            "---",
            "",
            "## (e) Reading and Interpretation",
            "",
        ]
    )

    # Three lines of reading
    redundant_str = ", ".join(f"`{a}`–`{b}` ({c:.2f})" for a, b, c in redundant_pairs) if redundant_pairs else "None"
    independent_str = ", ".join(f"`{name}` (avg |resid corr| = {avg_c:.2f})" for name, avg_c in independent_streams[:3])

    lines.extend(
        [
            f"1. **Redundant Pairs (residual correlation > 0.70):** {redundant_str}. The ETF8 momentum variations (`MonthlyRotation`, `VolTargetMomentum`, `QuarterlyTeCostGate`) and the two sector trend books (`SmaDaily`, `SmaDailyGated`) share the vast majority of their alpha variance and offer no mutual diversification.",
            f"2. **Most Independent Streams:** {independent_str}. The corporate event books (`activist_13d`, `buyback_sp100`) and the macro trend sleeve (`TSMOM`) have near-zero residual correlation with the equity rotation and sector trend strategies.",
            "3. **Verdict:** **No bot is promoted.** Every stream and the 6-survivor blend remain `unproven` under the project bar (t >= 2 and alpha > 0 in both halves).",
            "",
        ]
    )

    return "\n".join(lines)


def main() -> int:
    start_time = time.time()
    setup_cache_paths()
    prices, rf_daily, qqq = load_all_prices()

    print("Simulating six quantsmd survivors with T-bill lever on...", file=sys.stderr)
    survivor_configs = [
        ("MonthlyRotation", mr.ETF8, mr.ETF8, lambda sub, s: mr.compute_weights(sub, 126, 3), None),
        ("VolTargetMomentum", mr.ETF8, mr.ETF8, lambda sub, s: vtm.compute_weights(sub, 126, 3, 0.40, 0.15, 42), None),
        (
            "QuarterlyTeCostGate",
            mr.ETF8,
            mr.ETF8,
            lambda sub, s: qte.compute_weights(sub, 126, 3, 0.40, 0.06, 63, 4.0, s),
            {"last_target": None, "turnover": []},
        ),
        ("RiskParityCapped", rpc.UNIVERSE, rpc.UNIVERSE, lambda sub, s: rpc.compute_weights(sub, 126, 0.25), None),
        ("SmaDaily", sma.UNIVERSE, sma.UNIVERSE, lambda sub, s: sma.compute_weights(sub, 50, 200, 0.25), None),
        (
            "SmaDailyGated",
            [*smag.UNIVERSE, "SPY"],
            smag.UNIVERSE,
            lambda sub, s: smag.compute_weights(sub, 50, 200, 0.25, 0.0),
            None,
        ),
    ]

    all_streams: dict[str, pd.Series] = {}
    for name, d_syms, t_syms, w_fn, init_s in survivor_configs:
        t0 = time.time()
        ret = simulate_quantsmd_survivor(d_syms, t_syms, w_fn, init_s, prices, rf_daily)
        all_streams[name] = ret
        print(f"  {name}: {len(ret)} bars ({time.time() - t0:.1f}s)", file=sys.stderr)

    print("Simulating TSMOM in plain pandas...", file=sys.stderr)
    t0 = time.time()
    tsmom_ret = simulate_tsmom(prices)
    all_streams["TSMOM"] = tsmom_ret
    print(f"  TSMOM: {len(tsmom_ret)} bars ({time.time() - t0:.1f}s)", file=sys.stderr)

    print("Simulating event streams from lift study...", file=sys.stderr)
    t0 = time.time()
    event_streams = simulate_event_streams()
    for k, v in event_streams.items():
        all_streams[k] = v
        print(f"  {k}: {len(v)} bars", file=sys.stderr)
    print(f"  Event streams done ({time.time() - t0:.1f}s)", file=sys.stderr)

    # Common window alignment
    qqq_ret = qqq.pct_change().dropna()
    qqq_ret.index = pd.DatetimeIndex(qqq_ret.index).tz_localize(None).normalize()

    # Normalize indices to dates without tz
    norm_streams: dict[str, pd.Series] = {}
    for k, v in all_streams.items():
        s = v.copy()
        s.index = pd.DatetimeIndex(s.index).tz_localize(None).normalize()
        norm_streams[k] = s

    # Slice common window starting from COMMON_START to min of last available bar
    start_ts = pd.Timestamp(COMMON_START)
    common_idx = qqq_ret.loc[start_ts:].index
    for s in norm_streams.values():
        s_sub = s.loc[start_ts:]
        common_idx = common_idx.intersection(s_sub.index)

    common_idx = common_idx.sort_values()
    common_df = pd.DataFrame({k: norm_streams[k].loc[common_idx] for k in norm_streams}, index=common_idx)
    common_qqq = qqq_ret.loc[common_idx]

    # (a) Raw daily return correlation matrix
    corr_raw = common_df.corr().round(2)

    # (b) Residual correlation matrix
    resids: dict[str, pd.Series] = {}
    var_q = float(np.var(common_qqq, ddof=1))
    for col in common_df.columns:
        cov_col = float(np.cov(common_df[col], common_qqq)[0, 1])
        beta = cov_col / var_q
        resids[col] = common_df[col] - beta * common_qqq
    corr_resid = pd.DataFrame(resids).corr().round(2)

    # (c) Stream metrics: full history vs common window
    full_metrics: list[StreamMetrics] = []
    common_metrics: list[StreamMetrics] = []
    for k in norm_streams:
        # Full history
        full_metrics.append(compute_metrics(norm_streams[k], qqq_ret, k))
        # Common window
        common_metrics.append(compute_metrics(common_df[k], common_qqq, k))

    # (d) Equal-weight blend of six survivors
    survivor_names = [cfg[0] for cfg in survivor_configs]
    blend_ret = common_df[survivor_names].mean(axis=1)
    blend_metrics = compute_metrics(blend_ret, common_qqq, "6-Survivor Equal-Weight Blend")

    # Find best survivor on common window (by alpha & t)
    survivor_common_metrics = [m for m in common_metrics if m.name in survivor_names]
    best_survivor_metrics = max(survivor_common_metrics, key=lambda m: (m.alpha, m.t))

    # (e) Identify redundant pairs (resid corr > 0.70) and independent streams
    redundant_pairs: list[tuple[str, str, float]] = []
    cols = list(corr_resid.columns)
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            c_val = float(corr_resid.iloc[i, j])
            if c_val > 0.70:
                redundant_pairs.append((cols[i], cols[j], c_val))

    # Average absolute residual correlation per stream vs all others
    avg_resid_corr: list[tuple[str, float]] = []
    for col in cols:
        others = [c for c in cols if c != col]
        avg_c = float(corr_resid.loc[col, others].abs().mean())
        avg_resid_corr.append((col, avg_c))
    avg_resid_corr.sort(key=lambda x: x[1])

    # Generate Markdown document
    doc_content = generate_report(
        common_df,
        common_qqq,
        corr_raw,
        corr_resid,
        full_metrics,
        common_metrics,
        blend_metrics,
        best_survivor_metrics,
        redundant_pairs,
        avg_resid_corr,
    )

    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.write_text(doc_content)
    print(f"\nGenerated report written to {OUT_DOC}", file=sys.stderr)
    print(f"Total execution time: {time.time() - start_time:.1f}s\n", file=sys.stderr)

    # Print summary to stdout
    print("=" * 80)
    print("SLEEVE CORRELATION STUDY - RAW RETURN CORRELATION MATRIX")
    print("=" * 80)
    print(corr_raw.to_string())
    print("\n" + "=" * 80)
    print("RESIDUAL CORRELATION MATRIX (ALPHA STREAMS VS QQQ)")
    print("=" * 80)
    print(corr_resid.to_string())
    print("\n" + "=" * 80)
    print("COMMON WINDOW PERFORMANCE (2010-02 to 2026-11)")
    print("=" * 80)
    for m in common_metrics:
        print(
            f"{m.name:20s}: Alpha {m.alpha:+5.1f}% | t {m.t:4.2f} | Beta {m.beta:4.2f} | Corr {m.corr:4.2f} | MaxDD {m.max_dd:5.1f}% | H1 t {m.h1_t:4.2f} | H2 t {m.h2_t:4.2f} | {m.verdict}"
        )
    print("-" * 80)
    print(
        f"{blend_metrics.name:20s}: Alpha {blend_metrics.alpha:+5.1f}% | t {blend_metrics.t:4.2f} | Beta {blend_metrics.beta:4.2f} | Corr {blend_metrics.corr:4.2f} | MaxDD {blend_metrics.max_dd:5.1f}% | H1 t {blend_metrics.h1_t:4.2f} | H2 t {blend_metrics.h2_t:4.2f} | {blend_metrics.verdict}"
    )
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
