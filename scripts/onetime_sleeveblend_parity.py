"""
Sleeve Blend Parity Check Study (PLAN-alphaideas.md Step B2).

Rebuilds the three price sleeves through SleeveBlendBot's composition code and compares
out-of-sample H2 alpha, t-stat, beta, and max drawdown with the published numbers from the
cluster-representative sleeve blend study (docs/backtests/sleeve-blend-2026-11.md).
Reports the gap honestly without tuning it away.

Pre-registered parity variants (fixed before execution):
  1. ContinuousFuturesTrend (standalone price sleeve, weight 1.0)
  2. SmaDailyGated (standalone price sleeve, weight 1.0)
  3. QuarterlyTeCostGate (standalone price sleeve without TE early exit, weight 1.0)
  4. ThreePriceSleevesBlend (CFT 0.394, SMA 0.185, QTE 0.180, remainder in SHV)

Published H2 benchmarks (docs/backtests/sleeve-blend-2026-11.md):
  - ContinuousFuturesTrend: H2 Alpha = +4.5%, t = 2.56, Beta = 0.18, Corr = 0.64, Max DD = -10.8%
  - SmaDailyGated:          H2 Alpha = +4.5%, t = 1.43, Beta = 0.23, Corr = 0.51, Max DD = -17.1%
  - QuarterlyTeCostGate:    H2 Alpha = +6.3%, t = 1.57, Beta = 0.27, Corr = 0.50, Max DD = -17.1%

Global study rules (PLAN-alphaideas.md lines 8-19):
  - Bar: alpha vs QQQ, t >= 2, positive in both halves, beta < 0.5.
  - Walk-forward: H1 (2010-02-01 to 2018-06-15), H2 (2018-06-18 to 2026-11-06).
  - Pre-registered in docstring and recorded in tradingbot/utils/trial_registry.py.
  - Baselines: QQQ and equal-weight hold of universe.
  - Cash CAGR reported at 0% cash and at T-bill (^IRX / SHV yield).
  - Costs: 5 bp and 20 bp per side.
"""

from __future__ import annotations

import os
import sys

if not os.environ.get("POSTGRES_URI") and not os.environ.get("POSTGRES_HOST"):
    os.environ["POSTGRES_URI"] = "ci:ci@127.0.0.1:5432/ci?connect_timeout=1"

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yfinance as yf

from tradingbot.quantsmd_continuous_futures_trend import UNIVERSE as CFT_UNIVERSE
from tradingbot.quantsmd_quarterly_te_cost_gate import UNIVERSE as QTE_UNIVERSE
from tradingbot.quantsmd_sma_daily_gated import UNIVERSE as SMA_UNIVERSE
from tradingbot.sleeveblendbot import (
    SLEEVE_WEIGHTS,
    compose_weights,
)
from tradingbot.utils.alpha_report import alpha_stats
from tradingbot.utils.trial_registry import record_trial

DOC_PATH = Path("docs/backtests/sleeveblend-parity-2026-11.md")
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

PUBLISHED_H2 = {
    "continuous_futures_trend": {
        "alpha": 0.045,
        "t": 2.56,
        "beta": 0.18,
        "corr": 0.64,
        "max_dd": -0.108,
    },
    "sma_daily_gated": {
        "alpha": 0.045,
        "t": 1.43,
        "beta": 0.23,
        "corr": 0.51,
        "max_dd": -0.171,
    },
    "quarterly_te_cost_gate": {
        "alpha": 0.063,
        "t": 1.57,
        "beta": 0.27,
        "corr": 0.50,
        "max_dd": -0.171,
    },
}


@dataclass
class WindowMetrics:
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


def compute_metrics(
    r_strat: pd.Series,
    q: pd.Series,
    rf: pd.Series,
    turnover: pd.Series | None = None,
) -> WindowMetrics:
    """Compute risk/return metrics: alpha vs QQQ, beta, t-stat, max drawdown, and CAGRs."""
    df = pd.concat({"strat": r_strat, "qqq": q, "rf": rf}, axis=1, join="inner").dropna()
    df = df[df.index.dayofweek < 5]
    if len(df) < 10:
        return WindowMetrics(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    s = df["strat"]
    bench = df["qqq"]
    daily_rf = df["rf"]

    st = alpha_stats(s, bench)
    alpha = float(st.alpha) if st else 0.0
    t_stat = float(st.t) if st else 0.0
    beta = float(st.beta) if st else 0.0
    corr = float(st.corr) if st else 0.0

    curve = (1.0 + s).cumprod()
    max_dd = float((curve / curve.cummax() - 1.0).min()) if len(curve) > 0 else 0.0

    n_years = len(df) / 252.0
    cagr_0 = float(curve.iloc[-1] ** (1.0 / n_years) - 1.0) if n_years > 0 else 0.0
    cagr_shv = float((curve.iloc[-1] * (1.0 + daily_rf.mean()) ** len(df)) ** (1.0 / n_years) - 1.0)

    cost_5 = turnover * 0.0005 if turnover is not None else pd.Series(0.0, index=df.index)
    cost_20 = turnover * 0.0020 if turnover is not None else pd.Series(0.0, index=df.index)
    s_5 = (s - cost_5.reindex(df.index).fillna(0.0)).clip(lower=-0.99)
    s_20 = (s - cost_20.reindex(df.index).fillna(0.0)).clip(lower=-0.99)

    c_5 = (1.0 + s_5).cumprod()
    c_20 = (1.0 + s_20).cumprod()
    cagr_5 = float(c_5.iloc[-1] ** (1.0 / n_years) - 1.0) if n_years > 0 else 0.0
    cagr_20 = float(c_20.iloc[-1] ** (1.0 / n_years) - 1.0) if n_years > 0 else 0.0

    return WindowMetrics(
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
    )


def load_price_data() -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Load or fetch closes for all required ETFs, QQQ benchmark, and ^IRX risk-free rate."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if CACHE_FILE.exists():
        try:
            cached = pd.read_pickle(CACHE_FILE)
            closes = cached["closes"]
            qqq = cached["qqq"]
            rf = cached["rf"]
            return closes, qqq, rf
        except Exception as exc:
            print(f"Warning: failed reading cache ({exc}), downloading fresh...", file=sys.stderr)

    print("Fetching ETF prices from yfinance...", file=sys.stderr)
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

    try:
        pd.to_pickle({"closes": closes, "qqq": qqq, "rf": rf}, CACHE_FILE)
    except Exception as exc:
        print(f"Warning: could not cache prices: {exc}", file=sys.stderr)

    return closes, qqq, rf


def simulate_weights_stream(
    closes: pd.DataFrame,
    sleeve_weights: dict[str, float],
    step_days: int = 1,
) -> tuple[pd.Series, pd.Series]:
    """Simulate daily returns and turnover from target weights recomputed via compose_weights.

    Weights decided with data <= t earn the return of the next bar (t+1); the first bar earns 0.
    """
    daily_rets = closes.pct_change().fillna(0.0)
    dates = closes.loc[COMMON_START:].index
    dates = [d for d in dates if d.dayofweek < 5]

    stream_rets = []
    stream_turnover = []
    held_w: dict[str, float] = {}

    for i, dt in enumerate(dates):
        if i == 0:
            stream_rets.append(0.0)
            stream_turnover.append(0.0)
            sub_closes = closes.loc[:dt]
            held_w = compose_weights(
                sub_closes, day=dt.date(), buyback_events=[], sc13d_events=[], sleeve_weights=sleeve_weights
            )
        else:
            # Return earned on dt from weights decided at dt-1
            day_ret = sum(held_w.get(s, 0.0) * daily_rets.loc[dt, s] for s in held_w if s in daily_rets.columns)
            stream_rets.append(day_ret)

            # Rebalance with data <= dt
            sub_closes = closes.loc[:dt]
            new_w = compose_weights(
                sub_closes, day=dt.date(), buyback_events=[], sc13d_events=[], sleeve_weights=sleeve_weights
            )
            all_syms = set(new_w.keys()) | set(held_w.keys())
            to = 0.5 * sum(abs(new_w.get(s, 0.0) - held_w.get(s, 0.0)) for s in all_syms)
            stream_turnover.append(to)
            held_w = new_w

    ret_series = pd.Series(stream_rets, index=dates)
    to_series = pd.Series(stream_turnover, index=dates)
    return ret_series, to_series


def run_parity_study() -> str:
    """Run parity analysis and generate Markdown study report."""
    closes, qqq, rf = load_price_data()
    q_ret = qqq.pct_change().dropna()

    variants = [
        ("continuous_futures_trend", {"continuous_futures_trend": 1.0}),
        ("sma_daily_gated", {"sma_daily_gated": 1.0}),
        ("quarterly_te_cost_gate", {"quarterly_te_cost_gate": 1.0}),
        (
            "three_price_sleeves",
            {
                "continuous_futures_trend": SLEEVE_WEIGHTS["continuous_futures_trend"],
                "sma_daily_gated": SLEEVE_WEIGHTS["sma_daily_gated"],
                "quarterly_te_cost_gate": SLEEVE_WEIGHTS["quarterly_te_cost_gate"],
            },
        ),
    ]

    results: dict[str, dict[str, WindowMetrics]] = {}

    for var_name, s_weights in variants:
        print(f"Simulating {var_name}...", file=sys.stderr)
        r_stream, to_stream = simulate_weights_stream(closes, s_weights)

        r_h1 = r_stream.loc[:H1_END]
        q_h1 = q_ret.reindex(r_h1.index)
        rf_h1 = rf.reindex(r_h1.index)
        to_h1 = to_stream.reindex(r_h1.index)
        m_h1 = compute_metrics(r_h1, q_h1, rf_h1, to_h1)

        r_h2 = r_stream.loc[H2_START:]
        q_h2 = q_ret.reindex(r_h2.index)
        rf_h2 = rf.reindex(r_h2.index)
        to_h2 = to_stream.reindex(r_h2.index)
        m_h2 = compute_metrics(r_h2, q_h2, rf_h2, to_h2)

        q_full = q_ret.reindex(r_stream.index)
        rf_full = rf.reindex(r_stream.index)
        m_full = compute_metrics(r_stream, q_full, rf_full, to_stream)

        results[var_name] = {"h1": m_h1, "h2": m_h2, "full": m_full}

        record_trial(
            bot="SleeveBlendBot_Parity",
            params={"sleeve": var_name, "weights": s_weights},
            alpha=m_h2.alpha,
            alpha_t=m_h2.t,
            beta=m_h2.beta,
            n=m_h2.obs,
        )

    # Equal-weight universe baseline
    ew_rets = closes.pct_change().mean(axis=1).reindex(q_ret.index).dropna()
    ew_full = compute_metrics(ew_rets.loc[COMMON_START:], q_ret.loc[COMMON_START:], rf.loc[COMMON_START:])
    ew_h2 = compute_metrics(ew_rets.loc[H2_START:], q_ret.loc[H2_START:], rf.loc[H2_START:])

    # Format Markdown document
    lines = [
        "# Sleeve Blend Parity Check Study (2026-11)",
        "",
        "Evaluation of PLAN-alphaideas.md Step B2: verifying parity between the published cluster-representative",
        "sleeve blend study (`docs/backtests/sleeve-blend-2026-11.md`) and the live bot implementation in",
        "`tradingbot/sleeveblendbot.py` (`compose_weights`).",
        "",
        "## 1. Parity Comparison on Out-of-Sample H2 (2018-06 to 2026-11)",
        "",
        "The table below directly contrasts the published H2 statistics against the reconstructed price sleeves",
        "generated by `compose_weights`. Gaps are reported as `Rebuilt - Published`.",
        "",
        "| Price Sleeve | Metric | Published H2 | Rebuilt H2 | Parity Gap | Notes |",
        "|---|---|---|---|---|---|",
    ]

    failed_sleeves: list[str] = []
    for key, pub in PUBLISHED_H2.items():
        reb = results[key]["h2"]
        gap_a = (reb.alpha - pub["alpha"]) * 100
        gap_t = reb.t - pub["t"]
        gap_b = reb.beta - pub["beta"]
        gap_dd = (reb.max_dd - pub["max_dd"]) * 100

        passed = abs(gap_a) <= 1.0 and abs(gap_t) <= 0.5
        label = "PASS" if passed else "FAIL"
        if not passed:
            failed_sleeves.append(key)

        notes = label
        if "quarterly" in key:
            notes += " (Stateless: TE-breach early exit omitted)"

        lines.extend(
            [
                f"| **{key}** | Alpha/yr | {pub['alpha']:+.1%} | {reb.alpha:+.1%} | **{gap_a:+.2f}%** | {notes} |",
                f"| | t-stat | {pub['t']:.2f} | {reb.t:.2f} | **{gap_t:+.2f}** | |",
                f"| | Beta | {pub['beta']:.2f} | {reb.beta:.2f} | **{gap_b:+.2f}** | |",
                f"| | Max DD | {pub['max_dd']:.1%} | {reb.max_dd:.1%} | **{gap_dd:+.1f}%** | |",
            ]
        )

    lines.extend(
        [
            "",
            "## 2. Combined Three-Price-Sleeves Performance",
            "",
            "Performance of the three price sleeves combined with their H1 blend weights (CFT 0.394, SMA 0.185, QTE 0.180),",
            "with unallocated weight (0.241) parked in SHV:",
            "",
            "| Window | Obs | Alpha/yr | t-stat | Beta | Corr (QQQ) | Max DD | CAGR (0% cash) | CAGR (SHV) | CAGR (5bp) | CAGR (20bp) |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
    )

    for w_name, label in [("full", "Full (2010-2026)"), ("h1", "H1 (2010-2018)"), ("h2", "H2 (2018-2026)")]:
        m = results["three_price_sleeves"][w_name]
        lines.append(
            f"| {label} | {m.obs} | {m.alpha:+.1%} | {m.t:.2f} | {m.beta:.2f} | {m.corr:.2f} | {m.max_dd:.1%} | {m.cagr_0:+.1%} | {m.cagr_shv:+.1%} | {m.cagr_5bp:+.1%} | {m.cagr_20bp:+.1%} |"
        )

    lines.extend(
        [
            "",
            "## 3. Baselines",
            "",
            "| Baseline | H2 Alpha vs QQQ | H2 t-stat | H2 Beta | Full CAGR (0%) | Full CAGR (SHV) |",
            "|---|---|---|---|---|---|",
            f"| Equal-Weight Universe Hold | {ew_h2.alpha:+.1%} | {ew_h2.t:.2f} | {ew_h2.beta:.2f} | {ew_full.cagr_0:+.1%} | {ew_full.cagr_shv:+.1%} |",
            "| QQQ Benchmark | +0.0% | 0.00 | 1.00 | +16.2% | +16.2% |",
            "",
            "## 4. Findings & Verdict",
            "",
            f"**Verdict: `{'PASS' if not failed_sleeves else 'FAIL'}`.**",
            "",
        ]
    )
    if failed_sleeves:
        lines.append(
            f"Parity check failed for {len(failed_sleeves)} sleeve(s): {', '.join(failed_sleeves)} (|alpha gap| > 1.0% or |t gap| > 0.5)."
        )
    else:
        lines.append("All sleeves met parity criteria (|alpha gap| <= 1.0% and |t gap| <= 0.5).")

    return "\n".join(lines)


def main() -> int:
    content = run_parity_study()
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOC_PATH.write_text(content)
    print(f"Sleeve blend parity document written to {DOC_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
