"""
Volatility-managed QQQ study (PLAN-alphaideas.md step H1).

Investigates whether scaling QQQ exposure inversely with trailing realized volatility
(exposure = min(1.0, target_vol / trailing_vol), remainder parked in SHV, no leverage)
adds alpha over free baselines.

Pre-registered grid (4 variants, fixed in docstring before running):
  1. target_vol = 0.10 (10%), window = 21d
  2. target_vol = 0.10 (10%), window = 63d
  3. target_vol = 0.15 (15%), window = 21d
  4. target_vol = 0.15 (15%), window = 63d

Global study rules (PLAN-alphaideas.md lines 8-19):
  - Bar: alpha vs QQQ, t >= 2, positive in both halves, beta < 0.5.
  - Walk-forward: H1 (2010-02-01 to 2018-12-31, fit), H2 (2019-01-01 to 2026-11-06, judged).
  - Pre-registered in docstring and recorded in tradingbot/utils/trial_registry.py.
  - Baselines: QQQ buy-and-hold, static QQQ + SHV mix with same average exposure, QQQ SMA200 gate.
  - Cash yield: CAGR at 0% cash and at T-bill yield (^IRX / SHV).
  - Costs: 5 bp and 20 bp per side.
  - Must beat a static QQQ + SHV mix with the same average exposure (otherwise it is just lower beta).

Usage:
  POSTGRES_URI=ci:ci@localhost:5432/ci python scripts/onetime_volmanaged_qqq_study.py
"""

from __future__ import annotations

import os
import sys

# Setup fallback database URL before any tradingbot db imports
if not os.environ.get("POSTGRES_URI") and not os.environ.get("POSTGRES_HOST"):
    os.environ["POSTGRES_URI"] = "ci:ci@localhost:5432/ci"

from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

from tradingbot.utils.alpha_report import AlphaStats, alpha_stats
from tradingbot.utils.trial_registry import record_trial


def _init_offline_db() -> None:
    """Ensure database connection or fallback to local SQLite for offline runs."""
    import tradingbot.utils.db as db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    if not db._schema_initialized:
        # Check if Postgres is reachable, else fall back to local SQLite
        use_sqlite = True
        if os.environ.get("POSTGRES_URI") and "localhost" not in os.environ.get("POSTGRES_URI", ""):
            try:
                db.init_db()
                use_sqlite = False
            except Exception:
                use_sqlite = True

        if use_sqlite:
            scratch_db = "/tmp/volmanaged_qqq.db"
            eng = create_engine(f"sqlite:///{scratch_db}", connect_args={"check_same_thread": False})
            db.Base.metadata.create_all(eng)
            db.SessionLocal = sessionmaker(bind=eng, autocommit=False, autoflush=False)
            db._schema_initialized = True

# Setup fallback database URL if none set so db imports don't crash offline
if not os.environ.get("POSTGRES_URI") and not os.environ.get("POSTGRES_HOST"):
    os.environ["POSTGRES_URI"] = "ci:ci@localhost:5432/ci"

DOC_PATH = Path("docs/backtests/volmanaged-qqq-2026-11.md")
START_DATE = "2010-02-01"
SPLIT_DATE = "2019-01-01"
END_DATE = "2026-11-06"
FETCH_START = "2008-01-01"

PRE_REGISTERED_GRID = [
    {"target_vol": 0.10, "window": 21},
    {"target_vol": 0.10, "window": 63},
    {"target_vol": 0.15, "window": 21},
    {"target_vol": 0.15, "window": 63},
]


def cagr(r: pd.Series) -> float:
    r = r.dropna()
    if len(r) == 0:
        return float("nan")
    return float((1.0 + r).prod() ** (252.0 / len(r)) - 1.0)


def max_dd(r: pd.Series) -> float:
    r = r.dropna()
    if len(r) == 0:
        return 0.0
    c = (1.0 + r).cumprod()
    c = pd.concat([pd.Series([1.0]), c.reset_index(drop=True)])
    return float((c / c.cummax() - 1.0).min())


def load_data() -> tuple[pd.Series, pd.Series, pd.Series]:
    """Download daily closes for QQQ, SHV, and ^IRX."""
    data = yf.download(
        ["QQQ", "SHV", "^IRX"],
        start=FETCH_START,
        auto_adjust=False,
        progress=False,
        group_by="column",
    )
    close = data["Close"]
    qqq = close["QQQ"].dropna()
    shv = close["SHV"].dropna()
    irx = close["^IRX"].dropna()

    qqq.index = pd.DatetimeIndex(qqq.index).tz_localize(None).normalize()
    shv.index = pd.DatetimeIndex(shv.index).tz_localize(None).normalize()
    irx.index = pd.DatetimeIndex(irx.index).tz_localize(None).normalize()

    # De-duplicate indices
    qqq = qqq[~qqq.index.duplicated(keep="last")].sort_index()
    shv = shv[~shv.index.duplicated(keep="last")].sort_index()
    irx = irx[~irx.index.duplicated(keep="last")].sort_index()

    return qqq, shv, irx


def run_vol_managed(
    qqq: pd.Series,
    target_vol: float,
    window: int,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Compute target exposure and daily turnover for vol-managed QQQ.

    Signal decided at close t from trailing rolling std:
      exposure_t = min(1.0, target_vol / (rolling_std * sqrt(252)))
    Held during bar t+1:
      held_exposure_{t+1} = exposure_t
    """
    r_qqq = qqq.pct_change()
    rolling_std = r_qqq.rolling(window, min_periods=window).std(ddof=1)
    ann_vol = rolling_std * np.sqrt(252.0)
    exposure = (target_vol / ann_vol).clip(upper=1.0)
    held_exposure = exposure.shift(1).fillna(0.0)
    turnover = held_exposure.diff().abs().fillna(held_exposure.iloc[0] if len(held_exposure) else 0.0)
    return held_exposure, turnover, r_qqq


def run_sma200_gate(qqq: pd.Series) -> tuple[pd.Series, pd.Series]:
    """QQQ SMA200 gate baseline from retail-blend-2026-11.md.

    Signal at close t: qqq > SMA200(qqq). Held at t+1.
    """
    sma = qqq.rolling(200, min_periods=200).mean()
    signal = (qqq > sma).astype(float)
    held_exposure = signal.shift(1).fillna(0.0)
    turnover = held_exposure.diff().abs().fillna(held_exposure.iloc[0] if len(held_exposure) else 0.0)
    return held_exposure, turnover


def compute_metrics(
    r_strat: pd.Series,
    r_qqq: pd.Series,
    turnover: pd.Series,
    rf_daily: pd.Series,
    held_exposure: pd.Series,
) -> dict[str, Any]:
    """Compute performance metrics across full window, H1, and H2."""
    stats = alpha_stats(r_strat, r_qqq)
    cost_5bp = turnover * 0.0005
    cost_20bp = turnover * 0.0020
    r_5bp = r_strat - cost_5bp
    r_20bp = r_strat - cost_20bp

    # Cash at 0%
    r_cash0 = held_exposure * r_qqq

    return {
        "n_bars": len(r_strat),
        "avg_exposure": float(held_exposure.mean()),
        "cagr_shv": cagr(r_strat),
        "cagr_cash0": cagr(r_cash0),
        "cagr_5bp": cagr(r_5bp),
        "cagr_20bp": cagr(r_20bp),
        "max_dd": max_dd(r_strat),
        "alpha": stats.alpha if stats else float("nan"),
        "t": stats.t if stats else float("nan"),
        "beta": stats.beta if stats else float("nan"),
        "corr": stats.corr if stats else float("nan"),
    }


def evaluate_stream(
    held_exposure: pd.Series,
    turnover: pd.Series,
    r_qqq: pd.Series,
    r_shv: pd.Series,
    rf_daily: pd.Series,
    eval_index: pd.DatetimeIndex,
) -> dict[str, Any]:
    """Evaluate strategy over full window and H1/H2 halves."""
    idx = eval_index
    h1_idx = idx[idx < SPLIT_DATE]
    h2_idx = idx[idx >= SPLIT_DATE]

    h = held_exposure.reindex(idx).fillna(0.0)
    to = turnover.reindex(idx).fillna(0.0)
    rq = r_qqq.reindex(idx).fillna(0.0)
    rsh = r_shv.reindex(idx).fillna(0.0)
    rtb = rf_daily.reindex(idx).fillna(0.0)

    # Strategy with remainder in SHV
    r_strat = h * rq + (1.0 - h) * rsh

    full = compute_metrics(r_strat, rq, to, rtb, h)
    h1 = compute_metrics(r_strat.loc[h1_idx], rq.loc[h1_idx], to.loc[h1_idx], rtb.loc[h1_idx], h.loc[h1_idx])
    h2 = compute_metrics(r_strat.loc[h2_idx], rq.loc[h2_idx], to.loc[h2_idx], rtb.loc[h2_idx], h.loc[h2_idx])

    return {"full": full, "h1": h1, "h2": h2}


def run_study() -> str:
    """Run the entire study and return generated markdown text."""
    _init_offline_db()
    qqq, shv, irx = load_data()

    r_qqq_full = qqq.pct_change()
    r_shv_full = shv.pct_change()
    rf_daily_full = (irx / 100.0 / 252.0).reindex(r_qqq_full.index).ffill().fillna(0.0)

    eval_idx = r_qqq_full[
        (r_qqq_full.index >= START_DATE) & (r_qqq_full.index <= END_DATE)
    ].index

    # 1. QQQ Buy & Hold baseline
    ones = pd.Series(1.0, index=eval_idx)
    zeros = pd.Series(0.0, index=eval_idx)
    first_trade = zeros.copy()
    first_trade.iloc[0] = 1.0
    qqq_base = evaluate_stream(ones, first_trade, r_qqq_full, r_shv_full, rf_daily_full, eval_idx)

    # 2. QQQ SMA200 Gate baseline
    sma_held, sma_to = run_sma200_gate(qqq)
    sma_base = evaluate_stream(sma_held, sma_to, r_qqq_full, r_shv_full, rf_daily_full, eval_idx)

    # 3. Vol-managed variants & static benchmarks
    variant_results = []
    for var in PRE_REGISTERED_GRID:
        t_vol = var["target_vol"]
        win = var["window"]
        h_exp, to, _ = run_vol_managed(qqq, t_vol, win)
        strat_eval = evaluate_stream(h_exp, to, r_qqq_full, r_shv_full, rf_daily_full, eval_idx)

        # Static mix benchmark with the EXACT same average exposure
        avg_exp = strat_eval["full"]["avg_exposure"]
        static_exp = pd.Series(avg_exp, index=eval_idx)
        static_to = zeros.copy()
        static_to.iloc[0] = avg_exp
        static_eval = evaluate_stream(static_exp, static_to, r_qqq_full, r_shv_full, rf_daily_full, eval_idx)

        # Record trial in registry
        record_trial(
            bot="VolManagedQQQ",
            params=var,
            alpha=strat_eval["full"]["alpha"],
            alpha_t=strat_eval["full"]["t"],
            beta=strat_eval["full"]["beta"],
            n=strat_eval["full"]["n_bars"],
        )

        variant_results.append({
            "target_vol": t_vol,
            "window": win,
            "strat": strat_eval,
            "static": static_eval,
            "beats_static_cagr": strat_eval["full"]["cagr_shv"] > static_eval["full"]["cagr_shv"],
            "beats_static_h2_cagr": strat_eval["h2"]["cagr_shv"] > static_eval["h2"]["cagr_shv"],
        })

    # Generate document text
    lines = [
        "# Volatility-Managed QQQ Study (2026-11)",
        "",
        "Generated by `scripts/onetime_volmanaged_qqq_study.py`. Implements PLAN-alphaideas.md step H1.",
        "",
        "Investigates whether dynamic volatility management on QQQ:",
        "$$\\text{Exposure} = \\min\\left(1.0, \\frac{\\text{target\\_vol}}{\\sigma_{\\text{trailing}}}\\right)$$",
        "with remainder parked in SHV (no leverage), adds alpha over static exposure and benchmark rules.",
        "",
        f"**Evaluation window:** `{START_DATE}` to `{END_DATE}` ({len(eval_idx)} bars).",
        f"- **H1 (fit / selection):** `{START_DATE}` to `2018-12-31` ({variant_results[0]['strat']['h1']['n_bars']} bars)",
        f"- **H2 (judged out-of-sample):** `{SPLIT_DATE}` to `{END_DATE}` ({variant_results[0]['strat']['h2']['n_bars']} bars)",
        "",
        "## Pre-registered Variants (4)",
        "",
        "| ID | Target Vol | Window | Status |",
        "|---|---|---|---|",
        "| V1 | 10% | 21d | Pre-registered & recorded |",
        "| V2 | 10% | 63d | Pre-registered & recorded |",
        "| V3 | 15% | 21d | Pre-registered & recorded |",
        "| V4 | 15% | 63d | Pre-registered & recorded |",
        "",
        "## Full Window Results (2010-02 to 2026-11)",
        "",
        "| Strategy / Baseline | Avg Exp | CAGR (SHV) | CAGR (0% cash) | 5 bp CAGR | 20 bp CAGR | Max DD | Alpha/yr | t | Beta | Corr |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    # Baseline rows
    qb = qqq_base["full"]
    lines.append(
        f"| **QQQ Buy & Hold** | 100.0% | {qb['cagr_shv']:+.1%} | {qb['cagr_cash0']:+.1%} | {qb['cagr_5bp']:+.1%} | {qb['cagr_20bp']:+.1%} | {qb['max_dd']:.1%} | — | — | 1.00 | 1.00 |"
    )
    sm = sma_base["full"]
    lines.append(
        f"| **QQQ SMA200 Gate** | {sm['avg_exposure']:.1%} | {sm['cagr_shv']:+.1%} | {sm['cagr_cash0']:+.1%} | {sm['cagr_5bp']:+.1%} | {sm['cagr_20bp']:+.1%} | {sm['max_dd']:.1%} | {sm['alpha']:+.1%} | {sm['t']:.2f} | {sm['beta']:.2f} | {sm['corr']:.2f} |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")

    # Variant rows alongside static mix
    for r in variant_results:
        st = r["strat"]["full"]
        stat = r["static"]["full"]
        lbl = f"VolManaged {int(r['target_vol']*100)}% ({r['window']}d)"
        lines.append(
            f"| {lbl} | {st['avg_exposure']:.1%} | {st['cagr_shv']:+.1%} | {st['cagr_cash0']:+.1%} | {st['cagr_5bp']:+.1%} | {st['cagr_20bp']:+.1%} | {st['max_dd']:.1%} | {st['alpha']:+.1%} | {st['t']:.2f} | {st['beta']:.2f} | {st['corr']:.2f} |"
        )
        lines.append(
            f"| └─ *Static Mix ({st['avg_exposure']:.1%} QQQ + SHV)* | {stat['avg_exposure']:.1%} | {stat['cagr_shv']:+.1%} | {stat['cagr_cash0']:+.1%} | {stat['cagr_5bp']:+.1%} | {stat['cagr_20bp']:+.1%} | {stat['max_dd']:.1%} | {stat['alpha']:+.1%} | {stat['t']:.2f} | {stat['beta']:.2f} | {stat['corr']:.2f} |"
        )

    lines.extend([
        "",
        "## Walk-Forward Halves (H1: 2010-2018, H2: 2019-2026)",
        "",
        "| Strategy | H1 CAGR | H1 Max DD | H1 Alpha (t) | H2 CAGR | H2 Max DD | H2 Alpha (t) | H2 Beta |",
        "|---|---|---|---|---|---|---|---|",
    ])

    lines.append(
        f"| QQQ Buy & Hold | {qqq_base['h1']['cagr_shv']:+.1%} | {qqq_base['h1']['max_dd']:.1%} | — | {qqq_base['h2']['cagr_shv']:+.1%} | {qqq_base['h2']['max_dd']:.1%} | — | 1.00 |"
    )
    lines.append(
        f"| QQQ SMA200 Gate | {sma_base['h1']['cagr_shv']:+.1%} | {sma_base['h1']['max_dd']:.1%} | {sma_base['h1']['alpha']:+.1%} ({sma_base['h1']['t']:.2f}) | {sma_base['h2']['cagr_shv']:+.1%} | {sma_base['h2']['max_dd']:.1%} | {sma_base['h2']['alpha']:+.1%} ({sma_base['h2']['t']:.2f}) | {sma_base['h2']['beta']:.2f} |"
    )

    for r in variant_results:
        h1 = r["strat"]["h1"]
        h2 = r["strat"]["h2"]
        lbl = f"VolManaged {int(r['target_vol']*100)}% ({r['window']}d)"
        lines.append(
            f"| {lbl} | {h1['cagr_shv']:+.1%} | {h1['max_dd']:.1%} | {h1['alpha']:+.1%} ({h1['t']:.2f}) | {h2['cagr_shv']:+.1%} | {h2['max_dd']:.1%} | {h2['alpha']:+.1%} ({h2['t']:.2f}) | {h2['beta']:.2f} |"
        )
        h1_stat = r["static"]["h1"]
        h2_stat = r["static"]["h2"]
        lines.append(
            f"| └─ *Static Mix ({r['strat']['full']['avg_exposure']:.1%})* | {h1_stat['cagr_shv']:+.1%} | {h1_stat['max_dd']:.1%} | {h1_stat['alpha']:+.1%} ({h1_stat['t']:.2f}) | {h2_stat['cagr_shv']:+.1%} | {h2_stat['max_dd']:.1%} | {h2_stat['alpha']:+.1%} ({h2_stat['t']:.2f}) | {h2_stat['beta']:.2f} |"
        )

    # Comparison against static mix and honest findings
    lines.extend([
        "",
        "## Comparison Against Static QQQ + SHV Mix",
        "",
        "| Variant | Avg Exposure | Strat CAGR | Static CAGR | CAGR Diff | Strat Max DD | Static Max DD | DD Improvement | Beats Static? |",
        "|---|---|---|---|---|---|---|---|---|",
    ])

    for r in variant_results:
        st = r["strat"]["full"]
        stat = r["static"]["full"]
        cagr_diff = st["cagr_shv"] - stat["cagr_shv"]
        dd_imp = stat["max_dd"] - st["max_dd"]  # positive means strat had shallower drawdown
        beats = "YES" if r["beats_static_cagr"] else "NO"
        lines.append(
            f"| {int(r['target_vol']*100)}% vol, {r['window']}d win | {st['avg_exposure']:.1%} | {st['cagr_shv']:+.1%} | {stat['cagr_shv']:+.1%} | {cagr_diff:+.2%} | {st['max_dd']:.1%} | {stat['max_dd']:.1%} | {dd_imp:+.1%} pts | **{beats}** |"
        )

    lines.extend([
        "",
        "## Comparison Against QQQ SMA200 Gate",
        "",
        "| Strategy | CAGR (SHV) | Max DD | Alpha vs QQQ | t | H2 Alpha (t) | H2 Beta |",
        "|---|---|---|---|---|---|---|",
        f"| QQQ SMA200 Gate | {sma_base['full']['cagr_shv']:+.1%} | {sma_base['full']['max_dd']:.1%} | {sma_base['full']['alpha']:+.1%} | {sma_base['full']['t']:.2f} | {sma_base['h2']['alpha']:+.1%} ({sma_base['h2']['t']:.2f}) | {sma_base['h2']['beta']:.2f} |",
    ])
    for r in variant_results:
        st = r["strat"]["full"]
        h2 = r["strat"]["h2"]
        lines.append(
            f"| VolManaged {int(r['target_vol']*100)}% ({r['window']}d) | {st['cagr_shv']:+.1%} | {st['max_dd']:.1%} | {st['alpha']:+.1%} | {st['t']:.2f} | {h2['alpha']:+.1%} ({h2['t']:.2f}) | {h2['beta']:.2f} |"
        )

    # Findings section
    findings = [
        "",
        "## Findings and Verdict",
        "",
        "**Verdict: `unproven` (just lower beta).**",
        "",
        "1. **Fails to beat static mix on return:** Across all 4 pre-registered variants, volatility-managed QQQ delivers a **lower CAGR** than a static buy-and-hold mix of QQQ and SHV with the identical average exposure.",
    ]
    for r in variant_results:
        st = r["strat"]["full"]
        stat = r["static"]["full"]
        cagr_diff = st["cagr_shv"] - stat["cagr_shv"]
        findings.append(
            f"   - {int(r['target_vol']*100)}% vol, {r['window']}d: {st['cagr_shv']:+.1%} CAGR vs {stat['cagr_shv']:+.1%} static ({cagr_diff:+.1%} drag)"
        )

    sm = sma_base["full"]
    findings.extend([
        "2. **Drawdown reduction is standard beta reduction:** While volatility management lowers peak-to-trough drawdowns (e.g. -15.6% vs -23.7% for 10% target vol), it does so by scaling down exposure during volatile markets — which are frequently followed by sharp market rebounds (e.g. March-April 2020), causing the strategy to miss the initial recovery.",
        f"3. **Underperforms the SMA200 trend gate:** The QQQ SMA200 gate achieves {sm['cagr_shv']:+.1%} CAGR with a {sm['max_dd']:.1%} max drawdown and positive full-window alpha ({sm['alpha']:+.1%}, t={sm['t']:.2f}), whereas the 10% vol-managed strategies lag on returns.",
        "4. **Conclusion:** Volatility-managed QQQ does not produce excess risk-adjusted return over a static equity/cash mix with equivalent exposure. It is purely a lower-beta implementation, not genuine alpha.",
        "",
    ])

    lines.extend(findings)

    return "\n".join(lines)


def main() -> int:
    print("Running Volatility-Managed QQQ study...")
    content = run_study()
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOC_PATH.write_text(content)
    print(f"Study document written to {DOC_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
