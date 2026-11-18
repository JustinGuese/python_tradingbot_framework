"""
Blend Volatility and Leverage Scaling Study (PLAN-alphaideas.md Step B2).

Evaluates scaling the five-sleeve blend's positions until the book is X% invested,
for X in {Blend Own Average (~75.9%), 100%, 150%, 200%}.
Up to 100% requires no borrowing. Above 100%, charges (rf + 0.5%/yr) on the borrowed portion
and reports before- and after-financing performance.

Pre-registered scaling variants (fixed in docstring before execution):
  1. Base Blend (own average invested ~75.9% gross, unscaled)
  2. 100% Invested (1.0x equity, no borrowing, 0% cash)
  3. 150% Invested (1.5x equity, 50% borrowed, borrowing cost rf + 0.5%/yr)
  4. 200% Invested (2.0x equity, 100% borrowed, borrowing cost rf + 0.5%/yr)

Global study rules (PLAN-alphaideas.md lines 8-19):
  - Bar: alpha vs QQQ, t >= 2, positive in both halves, beta < 0.5.
  - Walk-forward: H1 (2010-02-01 to 2018-06-15), H2 (2018-06-18 to 2026-11-06).
  - Pre-registered in docstring and recorded in tradingbot/utils/trial_registry.py.
  - Baselines: QQQ and equal-weight hold.
  - Costs: 5 bp and 20 bp per side; cash CAGR at 0% and T-bill.
"""

from __future__ import annotations

import contextlib
import os
import sys

if not os.environ.get("POSTGRES_URI") and not os.environ.get("POSTGRES_HOST"):
    os.environ["POSTGRES_URI"] = "ci:ci@127.0.0.1:5432/ci?connect_timeout=1"

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

from tradingbot.quantsmd_continuous_futures_trend import UNIVERSE as CFT_UNIVERSE
from tradingbot.quantsmd_quarterly_te_cost_gate import UNIVERSE as QTE_UNIVERSE
from tradingbot.quantsmd_sma_daily_gated import UNIVERSE as SMA_UNIVERSE
from tradingbot.sleeveblendbot import (
    compose_weights,
)
from tradingbot.utils.alpha_report import alpha_stats
from tradingbot.utils.trial_registry import record_trial

DOC_PATH = Path("docs/backtests/blend-volscale-2026-11.md")
CACHE_DIR = Path(".cache")
CACHE_FILE = CACHE_DIR / "sleeveblend_prices.pkl"

COMMON_START = "2010-02-01"
H1_END = "2018-06-15"
H2_START = "2018-06-18"
FETCH_START = "2008-01-01"

PRICE_SYMBOLS = list(
    dict.fromkeys(
        [
            *CFT_UNIVERSE,
            *SMA_UNIVERSE,
            *QTE_UNIVERSE,
            "SPY",
            "SHV",
            "^IRX",
        ]
    )
)

PRE_REGISTERED_TARGETS = [
    {"label": "Base Blend (Own Avg ~75.9%)", "target_invested": 0.759, "borrow_frac": 0.0},
    {"label": "100% Invested (1.0x)", "target_invested": 1.000, "borrow_frac": 0.0},
    {"label": "150% Invested (1.5x)", "target_invested": 1.500, "borrow_frac": 0.5},
    {"label": "200% Invested (2.0x)", "target_invested": 2.000, "borrow_frac": 1.0},
]


@dataclass
class ScaleMetrics:
    obs: int
    alpha: float
    t: float
    beta: float
    corr: float
    max_dd: float
    cagr_0: float
    cagr_shv: float
    cagr_5bp: float
    cagr_20bp: float
    financing_drag_pct: float


def compute_scaled_metrics(
    r_strat: pd.Series,
    q: pd.Series,
    rf: pd.Series,
    borrow_frac: float = 0.0,
    turnover: pd.Series | None = None,
) -> ScaleMetrics:
    """Compute risk/return metrics including financing drag on borrowed capital."""
    df = pd.concat({"strat": r_strat, "qqq": q, "rf": rf}, axis=1, join="inner").dropna()
    df = df[df.index.dayofweek < 5]
    if len(df) < 10:
        return ScaleMetrics(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    daily_rf = df["rf"]
    # Borrow financing rate: rf + 0.5% annual spread
    daily_borrow_rate = daily_rf + (0.005 / 252.0)
    financing_drag_daily = borrow_frac * daily_borrow_rate
    annual_drag_pct = float(borrow_frac * (daily_rf.mean() * 252.0 + 0.005) * 100)

    # Net strategy returns after financing cost
    s_net = df["strat"] - financing_drag_daily
    bench = df["qqq"]

    st = alpha_stats(s_net, bench)
    alpha = float(st.alpha) if st else 0.0
    t_stat = float(st.t) if st else 0.0
    beta = float(st.beta) if st else 0.0
    corr = float(st.corr) if st else 0.0

    curve = (1.0 + s_net).cumprod()
    max_dd = float((curve / curve.cummax() - 1.0).min()) if len(curve) > 0 else 0.0

    n_years = len(df) / 252.0
    cagr_0 = float(curve.iloc[-1] ** (1.0 / n_years) - 1.0) if n_years > 0 else 0.0
    cagr_shv = float((curve.iloc[-1] * (1.0 + daily_rf.mean()) ** len(df)) ** (1.0 / n_years) - 1.0)

    cost_5 = turnover * 0.0005 if turnover is not None else pd.Series(0.0, index=df.index)
    cost_20 = turnover * 0.0020 if turnover is not None else pd.Series(0.0, index=df.index)
    s_5 = (s_net - cost_5.reindex(df.index).fillna(0.0)).clip(lower=-0.99)
    s_20 = (s_net - cost_20.reindex(df.index).fillna(0.0)).clip(lower=-0.99)

    c_5 = (1.0 + s_5).cumprod()
    c_20 = (1.0 + s_20).cumprod()
    cagr_5 = float(c_5.iloc[-1] ** (1.0 / n_years) - 1.0) if n_years > 0 else 0.0
    cagr_20 = float(c_20.iloc[-1] ** (1.0 / n_years) - 1.0) if n_years > 0 else 0.0

    return ScaleMetrics(
        obs=len(df),
        alpha=alpha,
        t=t_stat,
        beta=beta,
        corr=corr,
        max_dd=max_dd,
        cagr_0=cagr_0,
        cagr_shv=cagr_shv,
        cagr_5bp=cagr_5,
        cagr_20bp=cagr_20,
        financing_drag_pct=annual_drag_pct,
    )


def load_price_data() -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Load cached prices or fetch via yfinance."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if CACHE_FILE.exists():
        with contextlib.suppress(Exception):
            cached = pd.read_pickle(CACHE_FILE)
            return cached["closes"], cached["qqq"], cached["rf"]

    df = yf.download(PRICE_SYMBOLS, start=FETCH_START, auto_adjust=False, progress=False)
    close = df["Close"]
    close.index = pd.DatetimeIndex(close.index).tz_localize(None).normalize()
    close = close[~close.index.duplicated(keep="last")].sort_index()

    qqq = close["QQQ"].dropna()
    rf = (
        (close["^IRX"].dropna() / 100.0) / 252.0
        if "^IRX" in close.columns
        else pd.Series(0.04 / 252.0, index=close.index)
    )
    closes = close.drop(columns=["^IRX"], errors="ignore").ffill()

    with contextlib.suppress(Exception):
        pd.to_pickle({"closes": closes, "qqq": qqq, "rf": rf}, CACHE_FILE)

    return closes, qqq, rf


def simulate_scaled_blend(
    closes: pd.DataFrame,
    target_invested: float,
    dates: list[pd.Timestamp],
    base_weights: list[dict[str, float]],
) -> tuple[pd.Series, pd.Series]:
    """Simulate daily returns of the blend scaled to a target invested share.

    Weights decided with data <= t earn the return of the next bar (t+1); the first bar earns 0.
    """
    daily_rets = closes.pct_change().fillna(0.0)

    base_avg_invested = 0.759  # CFT 0.394 + SMA 0.185 + QTE 0.180
    scale_factor = target_invested / base_avg_invested

    stream_rets = []
    stream_turnover = []
    held_w: dict[str, float] = {}

    for i, (dt, w) in enumerate(zip(dates, base_weights, strict=False)):
        # Strip SHV parking before scaling the core positions
        core_w = {s: q * scale_factor for s, q in w.items() if s != "SHV"}

        # If target <= 1.0, uninvested equity earns SHV / cash yield
        if target_invested <= 1.0:
            core_w["SHV"] = max(0.0, 1.0 - sum(core_w.values()))

        if i == 0:
            stream_rets.append(0.0)
            stream_turnover.append(0.0)
            held_w = core_w
        else:
            day_ret = sum(held_w.get(s, 0.0) * daily_rets.loc[dt, s] for s in held_w if s in daily_rets.columns)
            stream_rets.append(day_ret)

            all_syms = set(core_w.keys()) | set(held_w.keys())
            to = 0.5 * sum(abs(core_w.get(s, 0.0) - held_w.get(s, 0.0)) for s in all_syms)
            stream_turnover.append(to)
            held_w = core_w

    ret_series = pd.Series(stream_rets, index=dates)
    to_series = pd.Series(stream_turnover, index=dates)
    return ret_series, to_series


def run_volscale_study() -> str:
    """Run volatility scaling and leverage study, writing docs/backtests/blend-volscale-2026-11.md."""
    closes, qqq, rf = load_price_data()
    q_ret = qqq.pct_change().dropna()

    dates = closes.loc[COMMON_START:].index
    dates = [d for d in dates if d.dayofweek < 5]
    print("Precomputing base blend weights...", file=sys.stderr)
    base_weights = [compose_weights(closes.loc[:dt], day=dt.date(), buyback_events=[], sc13d_events=[]) for dt in dates]

    results: list[dict[str, Any]] = []

    for cfg in PRE_REGISTERED_TARGETS:
        label = cfg["label"]
        tgt = cfg["target_invested"]
        borrow = cfg["borrow_frac"]
        print(f"Simulating {label}...", file=sys.stderr)

        r_stream, to_stream = simulate_scaled_blend(closes, tgt, dates, base_weights)

        # Before financing
        m_full_raw = compute_scaled_metrics(r_stream, q_ret, rf, borrow_frac=0.0, turnover=to_stream)
        m_h1_raw = compute_scaled_metrics(r_stream.loc[:H1_END], q_ret, rf, borrow_frac=0.0, turnover=to_stream)
        m_h2_raw = compute_scaled_metrics(r_stream.loc[H2_START:], q_ret, rf, borrow_frac=0.0, turnover=to_stream)

        # After financing (borrowed portion charged rf + 0.5%/yr)
        m_full_fin = compute_scaled_metrics(r_stream, q_ret, rf, borrow_frac=borrow, turnover=to_stream)
        m_h1_fin = compute_scaled_metrics(r_stream.loc[:H1_END], q_ret, rf, borrow_frac=borrow, turnover=to_stream)
        m_h2_fin = compute_scaled_metrics(r_stream.loc[H2_START:], q_ret, rf, borrow_frac=borrow, turnover=to_stream)

        results.append(
            {
                "cfg": cfg,
                "raw": {"h1": m_h1_raw, "h2": m_h2_raw, "full": m_full_raw},
                "fin": {"h1": m_h1_fin, "h2": m_h2_fin, "full": m_full_fin},
            }
        )

        record_trial(
            bot="Blend_VolScale_Study",
            params=cfg,
            alpha=m_h2_fin.alpha,
            alpha_t=m_h2_fin.t,
            beta=m_h2_fin.beta,
            n=m_h2_fin.obs,
        )

    # Format Markdown document
    lines = [
        "# Sleeve Blend Leverage and Volatility Scaling Study (2026-11)",
        "",
        "Evaluation of PLAN-alphaideas.md Step B2: evaluating the risk, return, beta, and financing drag of raising",
        "the five-sleeve blend's invested equity share across {Base (~75.9%), 100%, 150%, 200%}.",
        "",
        "## 1. Study Specification & Rules",
        "",
        "- **Pre-registered Variants (4):** Invested equity share in {75.9% (Base), 100%, 150%, 200%}.",
        "- **Borrow Financing Model:** Up to 100% requires no borrowing. Above 100%, borrowed equity incurs",
        "  financing cost at `^IRX + 0.5%/yr` spread. Evaluated both before and after financing drag.",
        "- **Framework Constraints Verified:**",
        "  - `tradingbot/utils/short_carry.py` accrues short borrow fees and dividends in lieu only (no margin borrowing",
        "    interest on cash, no debit interest).",
        "  - `tradingbot/utils/portfolio_manager.py` enforces `spendable_cash` capping buys at equity; long-only paper",
        "    bots cannot hold >100% gross exposure.",
        "- **Global Bar (CLAUDE.md):** alpha vs QQQ, t >= 2, positive in both halves, beta < 0.50.",
        "",
        "## 2. Scaling Results (Net of Financing Drag)",
        "",
        "| Invested Level | Borrowed | Financing Drag | H1 Alpha (t) | H2 Alpha (t) | H2 Beta | H2 Max DD | Full CAGR (SHV) | Full Max DD |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    for item in results:
        cfg = item["cfg"]
        fin_h1 = item["fin"]["h1"]
        fin_h2 = item["fin"]["h2"]
        fin_full = item["fin"]["full"]

        lines.append(
            f"| **{cfg['label']}** | {cfg['borrow_frac']:.0%} | -{fin_full.financing_drag_pct:.2f}%/yr | "
            f"{fin_h1.alpha:+.1%} ({fin_h1.t:.2f}) | {fin_h2.alpha:+.1%} ({fin_h2.t:.2f}) | **{fin_h2.beta:.2f}** | "
            f"{fin_h2.max_dd:.1%} | {fin_full.cagr_shv:+.1%} | **{fin_full.max_dd:.1%}** |"
        )

    lines.extend(
        [
            "",
            "## 3. Detailed Financing Drag Impact",
            "",
            "Comparison of gross metrics before financing interest versus net metrics after `rf + 0.5%/yr` margin interest:",
            "",
            "| Invested Level | Financing Status | Full CAGR | Full Alpha vs QQQ | t-stat | Full Max DD |",
            "|---|---|---|---|---|---|",
        ]
    )

    for item in results:
        cfg = item["cfg"]
        raw = item["raw"]["full"]
        fin = item["fin"]["full"]

        lines.append(
            f"| {cfg['label']} | Before Financing | {raw.cagr_shv:+.1%} | {raw.alpha:+.1%} | {raw.t:.2f} | {raw.max_dd:.1%} |"
        )
        if cfg["borrow_frac"] > 0:
            lines.append(
                f"| {cfg['label']} | **After Financing (-{fin.financing_drag_pct:.2f}%/yr)** | **{fin.cagr_shv:+.1%}** | **{fin.alpha:+.1%}** | **{fin.t:.2f}** | **{fin.max_dd:.1%}** |"
            )

    lines.extend(
        [
            "",
            "## 4. Policy Decision & Verdict",
            "",
        ]
    )

    passing_variants = [
        item["cfg"]["label"]
        for item in results
        if item["cfg"]["target_invested"] > 1.0
        and item["fin"]["h2"].beta < 0.5
        and item["fin"]["h2"].t >= 2.0
        and item["fin"]["h1"].alpha > 0
        and item["fin"]["h2"].alpha > 0
    ]

    if passing_variants:
        lines.append(f"**Decision: Leveraged variant(s) passed promotion bar: {', '.join(passing_variants)}.**\n")
    else:
        lines.append(
            "**Decision: No leveraged variant passed the promotion bar (beta < 0.5, t >= 2.0, positive alpha in both halves). B2 remains a study only.**\n"
        )

    lines.append("Observed metrics by target allocation:")
    for item in results:
        cfg = item["cfg"]
        fin_h2 = item["fin"]["h2"]
        fin_full = item["fin"]["full"]
        lines.append(
            f"- **{cfg['label']}**: H2 Beta = {fin_h2.beta:.2f}, H2 t = {fin_h2.t:.2f}, Full Max DD = {fin_full.max_dd:.1%}, Financing Drag = {fin_full.financing_drag_pct:.2f}%/yr"
        )
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    content = run_volscale_study()
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOC_PATH.write_text(content)
    print(f"Blend volscale study document written to {DOC_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
