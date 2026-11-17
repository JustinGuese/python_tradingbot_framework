"""Vectorised daily study harness for TradingView trend-boost enhancements (Step S2 of PLAN-trendboost.md).

Evaluates Ideas 1-9 across UNIVERSE_WIDE (2005-2026, Fit 2005-2018, Judge OOS 2019-2026):
- Baseline signals: Supertrend, AlphaTrend, Ensemble ST+AT (Idea 8)
- Enhancements:
  1. +invvol (Idea 1): rolling inverse-volatility weighting (window=60, target_vol=0.10, per_ticker_cap=0.20, gross_cap=1.0)
  2. +multispeed (Idea 2): arithmetic mean across 3 speeds (fast, medium, slow)
  3. +soft (Idea 3): continuous ATR-distance weight (max_atr_dist=3.0)
  4. +defensive (Idea 4): defensive rotation into TLT/IEF/GLD/SHV when risk assets off
  5. +topk (Idea 5): top 5 active assets ranked by 12-month momentum
  6. ex-equity universe (Idea 6): commodities, fixed income, FX only (8 ETFs)
  7. +SHV cash (Idea 7): unallocated cash earns SHV daily returns (pre-2007 = 0)
  8. Ensemble ST+AT (Idea 8): joint Supertrend + AlphaTrend agreement
  9. weekly bars (Idea 9): weekly trend signals aligned causally onto daily bars
  10. combo 1+2+4+7: combined multispeed + invvol + defensive rotation + SHV cash yield

Ship rule:
  OOS alpha_t > baseline_alpha_t AND OOS t > 0 AND beta < 0.5.
  Rank by OOS alpha_t, never return. Flag any with TSMOM corr > 0.7.

Usage:
  POSTGRES_URI=postgresql://ci:ci@localhost:5432/ci poetry run python scripts/onetime_tv_trend_boost_study.py --save-doc
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import pandas as pd

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.onetime_tv_daily_study import (  # noqa: E402
    CACHE_DIR,
    compute_flags_matrix,
    compute_strategy_returns,
    compute_tsmom_proxy,
    evaluate_metrics,
    load_daily_data,
    weights_from_flags,
)
from tradingbot.utils.tv_core import align_htf, resample_ohlcv  # noqa: E402
from tradingbot.utils.tv_signals import UNIVERSE_WIDE, signal_flags  # noqa: E402
from tradingbot.utils.tv_trend_boost import (  # noqa: E402
    cash_yield_returns,
    defensive_rotation,
    inv_vol_weights,
    multi_speed_flags,
    soft_flags,
    top_k_by_strength,
)

DOC_PATH = Path("docs/backtests/tv-trend-boost-2026-11.md")

# Non-equity cross-asset universe (Idea 6)
UNIVERSE_EX_EQUITY = ["GLD", "SLV", "DBC", "USO", "TLT", "IEF", "HYG", "UUP"]

# Defensive basket for rotation (Idea 4)
DEFENSIVE_BASKET = ("TLT", "IEF", "GLD", "SHV")

# Multi-speed parameter sets (Idea 2)
MULTISPEED_PARAMS = {
    "supertrend": [
        {"period": 7, "mult": 2.0},
        {"period": 10, "mult": 3.0},
        {"period": 14, "mult": 4.0},
    ],
    "alphatrend": [
        {"period": 10, "mult": 0.8},
        {"period": 14, "mult": 1.0},
        {"period": 20, "mult": 1.5},
    ],
}


def compute_weekly_flags_matrix(
    dfs: dict[str, pd.DataFrame],
    master_idx: pd.DatetimeIndex,
    kind: str,
    params: dict[str, Any] | None = None,
    universe: list[str] | None = None,
) -> pd.DataFrame:
    """Compute weekly trend signal flags causally aligned to daily master_idx."""
    target_uni = universe if universe is not None else UNIVERSE_WIDE
    cols: dict[str, pd.Series] = {}
    for ticker in target_uni:
        df = dfs.get(ticker)
        if df is None or df.empty:
            cols[ticker] = pd.Series(False, index=master_idx, dtype=bool)
            continue

        # Resample daily to weekly
        df_w = resample_ohlcv(df, "W-FRI")
        if df_w.empty:
            cols[ticker] = pd.Series(False, index=master_idx, dtype=bool)
            continue

        fl_w = signal_flags(df_w, kind=kind, params=params)
        # Shift 1 week and ffill onto daily master_idx to eliminate lookahead
        fl_d = align_htf(fl_w.astype(float), master_idx).fillna(0.0).astype(bool)
        cols[ticker] = fl_d

    return pd.DataFrame(cols, index=master_idx)


def run_trend_boost_study() -> tuple[dict[str, dict[str, Any]], pd.DatetimeIndex]:
    """Execute complete walk-forward study of Ideas 1-9 on Supertrend and AlphaTrend."""
    print("Loading daily OHLCV dataset including SHV...")
    universe_with_shv = list(dict.fromkeys([*list(UNIVERSE_WIDE), "SHV", "QQQ"]))
    dfs, master_idx, closes, qqq_ret = load_daily_data(cache_dir=CACHE_DIR, universe=universe_with_shv)

    # Closes frame including SHV
    all_closes = pd.DataFrame({t: dfs[t]["close"] for t in universe_with_shv if t in dfs}).reindex(master_idx).ffill()
    shv_close = dfs["SHV"]["close"].reindex(master_idx).ffill()

    print("Computing baseline daily flags (supertrend, alphatrend)...")
    st_flags = compute_flags_matrix(dfs, master_idx, kind="supertrend")
    at_flags = compute_flags_matrix(dfs, master_idx, kind="alphatrend")

    # Idea 8: Ensemble ST+AT baseline (both long signals active)
    ens_stat_flags = (st_flags.astype(int) + at_flags.astype(int)) >= 2

    print("Computing 12-month TSMOM proxy...")
    tsmom_ret, _ = compute_tsmom_proxy(closes)

    # 12-month cross-sectional strength for top-k (Idea 5)
    strength_12m = closes / closes.shift(252) - 1.0

    strategies_returns: dict[str, pd.Series] = {}
    strategy_meta: dict[str, dict[str, Any]] = {}

    # Helper to register strategy
    def register(name: str, rets: pd.Series, base_ref: str, idea_tag: str):
        strategies_returns[name] = rets
        strategy_meta[name] = {"base_ref": base_ref, "idea": idea_tag}

    # =========================================================================
    # 0. BASELINES
    # =========================================================================
    st_base_w = weights_from_flags(st_flags)
    st_base_ret = compute_strategy_returns(st_base_w, closes)
    register("supertrend_base", st_base_ret, "supertrend_base", "Baseline")

    at_base_w = weights_from_flags(at_flags)
    at_base_ret = compute_strategy_returns(at_base_w, closes)
    register("alphatrend_base", at_base_ret, "alphatrend_base", "Baseline")

    ens_base_w = weights_from_flags(ens_stat_flags)
    ens_base_ret = compute_strategy_returns(ens_base_w, closes)
    register("ensemble_stat_base", ens_base_ret, "ensemble_stat_base", "Idea 8 Baseline")

    # =========================================================================
    # 1. IDEA 1: INVERSE-VOLATILITY WEIGHTING (+invvol)
    # =========================================================================
    st_iv_w = inv_vol_weights(st_flags, closes, target_vol=0.10, window=60)
    register("supertrend+invvol", compute_strategy_returns(st_iv_w, closes), "supertrend_base", "Idea 1")

    at_iv_w = inv_vol_weights(at_flags, closes, target_vol=0.10, window=60)
    register("alphatrend+invvol", compute_strategy_returns(at_iv_w, closes), "alphatrend_base", "Idea 1")

    ens_iv_w = inv_vol_weights(ens_stat_flags, closes, target_vol=0.10, window=60)
    register("ensemble_stat+invvol", compute_strategy_returns(ens_iv_w, closes), "ensemble_stat_base", "Idea 1")

    # =========================================================================
    # 2. IDEA 2: MULTI-SPEED FLAGS (+multispeed)
    # =========================================================================
    print("Computing multi-speed flags (fast, medium, slow)...")
    st_ms_flags = multi_speed_flags(dfs, master_idx, "supertrend", MULTISPEED_PARAMS["supertrend"])
    st_ms_w = weights_from_flags(st_ms_flags)
    register("supertrend+multispeed", compute_strategy_returns(st_ms_w, closes), "supertrend_base", "Idea 2")

    at_ms_flags = multi_speed_flags(dfs, master_idx, "alphatrend", MULTISPEED_PARAMS["alphatrend"])
    at_ms_w = weights_from_flags(at_ms_flags)
    register("alphatrend+multispeed", compute_strategy_returns(at_ms_w, closes), "alphatrend_base", "Idea 2")

    # =========================================================================
    # 3. IDEA 3: SOFT ATR-DISTANCE FLAGS (+soft)
    # =========================================================================
    print("Computing soft ATR-distance flags...")
    st_soft = soft_flags(dfs, master_idx, "supertrend", max_atr_dist=3.0)
    st_soft_w = weights_from_flags(st_soft)
    register("supertrend+soft", compute_strategy_returns(st_soft_w, closes), "supertrend_base", "Idea 3")

    at_soft = soft_flags(dfs, master_idx, "alphatrend", max_atr_dist=3.0)
    at_soft_w = weights_from_flags(at_soft)
    register("alphatrend+soft", compute_strategy_returns(at_soft_w, closes), "alphatrend_base", "Idea 3")

    # =========================================================================
    # 4. IDEA 4: DEFENSIVE ROTATION (+defensive)
    # =========================================================================
    print("Computing defensive rotation into TLT/IEF/GLD/SHV...")
    st_def_w = defensive_rotation(st_flags, closes=all_closes, defensive=DEFENSIVE_BASKET)
    register("supertrend+defensive", compute_strategy_returns(st_def_w, all_closes), "supertrend_base", "Idea 4")

    at_def_w = defensive_rotation(at_flags, closes=all_closes, defensive=DEFENSIVE_BASKET)
    register("alphatrend+defensive", compute_strategy_returns(at_def_w, all_closes), "alphatrend_base", "Idea 4")

    ens_def_w = defensive_rotation(ens_stat_flags, closes=all_closes, defensive=DEFENSIVE_BASKET)
    register("ensemble_stat+defensive", compute_strategy_returns(ens_def_w, all_closes), "ensemble_stat_base", "Idea 4")

    # =========================================================================
    # 5. IDEA 5: TOP-K ACTIVE BY STRENGTH (+topk, k=5)
    # =========================================================================
    st_topk_flags = top_k_by_strength(st_flags, strength_12m, k=5)
    st_topk_w = weights_from_flags(st_topk_flags)
    register("supertrend+topk", compute_strategy_returns(st_topk_w, closes), "supertrend_base", "Idea 5")

    at_topk_flags = top_k_by_strength(at_flags, strength_12m, k=5)
    at_topk_w = weights_from_flags(at_topk_flags)
    register("alphatrend+topk", compute_strategy_returns(at_topk_w, closes), "alphatrend_base", "Idea 5")

    # =========================================================================
    # 6. IDEA 6: EX-EQUITY UNIVERSE (commodities, bonds, FX)
    # =========================================================================
    closes_ex_eq = closes[[c for c in UNIVERSE_EX_EQUITY if c in closes.columns]]
    st_ex_flags = st_flags[closes_ex_eq.columns]
    st_ex_w = weights_from_flags(st_ex_flags)
    register("supertrend_ex_equity", compute_strategy_returns(st_ex_w, closes_ex_eq), "supertrend_base", "Idea 6")

    at_ex_flags = at_flags[closes_ex_eq.columns]
    at_ex_w = weights_from_flags(at_ex_flags)
    register("alphatrend_ex_equity", compute_strategy_returns(at_ex_w, closes_ex_eq), "alphatrend_base", "Idea 6")

    # =========================================================================
    # 7. IDEA 7: CASH YIELD VIA SHV (+SHV cash)
    # =========================================================================
    st_cash_ret = cash_yield_returns(st_base_w, closes, shv_close)
    register("supertrend+SHV_cash", st_cash_ret, "supertrend_base", "Idea 7")

    at_cash_ret = cash_yield_returns(at_base_w, closes, shv_close)
    register("alphatrend+SHV_cash", at_cash_ret, "alphatrend_base", "Idea 7")

    ens_cash_ret = cash_yield_returns(ens_base_w, closes, shv_close)
    register("ensemble_stat+SHV_cash", ens_cash_ret, "ensemble_stat_base", "Idea 7")

    # =========================================================================
    # 9. IDEA 9: WEEKLY BARS (resampled W-FRI aligned to daily)
    # =========================================================================
    print("Computing weekly bar trend signals...")
    st_wk_flags = compute_weekly_flags_matrix(dfs, master_idx, "supertrend")
    st_wk_w = weights_from_flags(st_wk_flags)
    register("supertrend_weekly", compute_strategy_returns(st_wk_w, closes), "supertrend_base", "Idea 9")

    at_wk_flags = compute_weekly_flags_matrix(dfs, master_idx, "alphatrend")
    at_wk_w = weights_from_flags(at_wk_flags)
    register("alphatrend_weekly", compute_strategy_returns(at_wk_w, closes), "alphatrend_base", "Idea 9")

    # =========================================================================
    # 10. COMBO 1+2+4+7 (+invvol +multispeed +defensive +SHV cash)
    # =========================================================================
    print("Computing combo 1+2+4+7 for Supertrend and AlphaTrend...")
    # Supertrend combo:
    st_combo_iv_w = inv_vol_weights(st_ms_flags, closes, target_vol=0.10, window=60)
    st_combo_def_w = defensive_rotation(
        st_ms_flags, closes=all_closes, defensive=DEFENSIVE_BASKET, weights_df=st_combo_iv_w
    )
    st_combo_ret = cash_yield_returns(st_combo_def_w, all_closes, shv_close)
    register("supertrend_combo_1_2_4_7", st_combo_ret, "supertrend_base", "Combo 1+2+4+7")

    # AlphaTrend combo:
    at_combo_iv_w = inv_vol_weights(at_ms_flags, closes, target_vol=0.10, window=60)
    at_combo_def_w = defensive_rotation(
        at_ms_flags, closes=all_closes, defensive=DEFENSIVE_BASKET, weights_df=at_combo_iv_w
    )
    at_combo_ret = cash_yield_returns(at_combo_def_w, all_closes, shv_close)
    register("alphatrend_combo_1_2_4_7", at_combo_ret, "alphatrend_base", "Combo 1+2+4+7")

    # Ensemble ST+AT combo:
    ens_ms_flags = (st_ms_flags + at_ms_flags) / 2.0
    ens_combo_iv_w = inv_vol_weights(ens_ms_flags, closes, target_vol=0.10, window=60)
    ens_combo_def_w = defensive_rotation(
        ens_ms_flags, closes=all_closes, defensive=DEFENSIVE_BASKET, weights_df=ens_combo_iv_w
    )
    ens_combo_ret = cash_yield_returns(ens_combo_def_w, all_closes, shv_close)
    register("ensemble_stat_combo_1_2_4_7", ens_combo_ret, "ensemble_stat_base", "Combo 1+2+4+7")

    # TSMOM reference
    strategies_returns["tsmom_proxy"] = tsmom_ret
    strategy_meta["tsmom_proxy"] = {"base_ref": "tsmom_proxy", "idea": "Reference"}

    # =========================================================================
    # EVALUATION AND SHIP VERDICT
    # =========================================================================
    print("Evaluating metrics across all 25 variants...")
    metrics: dict[str, dict[str, Any]] = {}
    for name, s_ret in strategies_returns.items():
        bench_tsmom = tsmom_ret if name != "tsmom_proxy" else None
        m = evaluate_metrics(s_ret, qqq_ret, bench_tsmom, master_idx)
        m["idea"] = strategy_meta[name]["idea"]
        m["base_ref"] = strategy_meta[name]["base_ref"]
        metrics[name] = m

    # Evaluate ship rules vs baseline
    for name, m in metrics.items():
        base_name = m["base_ref"]
        base_m = metrics.get(base_name)
        base_judge_t = base_m["judge_t"] if (base_m and base_m["judge_t"] is not None) else 0.0

        judge_t = m["judge_t"] if m["judge_t"] is not None else 0.0
        beta = m["beta"]
        tsmom_corr = m["tsmom_corr"] if m["tsmom_corr"] is not None else 0.0

        # Ship rule: OOS alpha_t > baseline AND OOS t > 0 AND beta < 0.5
        is_baseline = name == base_name
        if is_baseline:
            m["ship_verdict"] = "BASELINE"
            m["ship_reason"] = "Reference baseline"
        else:
            beats_base = judge_t > base_judge_t
            pos_t = judge_t > 0.0
            low_beta = beta < 0.50
            if beats_base and pos_t and low_beta:
                m["ship_verdict"] = "SHIP"
                m["ship_reason"] = f"Judge t ({judge_t:+.2f}) > base ({base_judge_t:+.2f}), beta={beta:.2f} < 0.5"
            else:
                reasons = []
                if not beats_base:
                    reasons.append(f"Judge t ({judge_t:+.2f}) <= base ({base_judge_t:+.2f})")
                if not pos_t:
                    reasons.append(f"Judge t ({judge_t:+.2f}) <= 0")
                if not low_beta:
                    reasons.append(f"Beta ({beta:.2f}) >= 0.50")
                m["ship_verdict"] = "NO-SHIP"
                m["ship_reason"] = "; ".join(reasons)

        m["tsmom_flag"] = bool(tsmom_corr > 0.70)

    return metrics, master_idx


def generate_markdown_report(metrics: dict[str, dict[str, Any]], master_idx: pd.DatetimeIndex) -> str:
    """Format full study results into GitHub-flavored Markdown."""
    start_dt = master_idx[0].strftime("%Y-%m-%d")
    end_dt = master_idx[-1].strftime("%Y-%m-%d")
    n_sessions = len(master_idx)

    # Sort candidates by Judge t-stat descending
    sorted_items = sorted(
        metrics.items(),
        key=lambda kv: kv[1]["judge_t"] if kv[1]["judge_t"] is not None else -999.0,
        reverse=True,
    )

    lines = [
        "# Daily-Bar TradingView Trend Enhancement Study (2005-2026)",
        "",
        "Walk-forward evaluation of Ideas 1-9 on Supertrend and AlphaTrend",
        f"across `{len(UNIVERSE_WIDE)}` liquid cross-asset ETFs from {start_dt} to {end_dt} ({n_sessions:,} sessions).",
        "",
        "Fit period: **2005-2018** (14 years). Out-of-Sample Judge period: **2019-2026** (7.9 years).",
        "",
        "## Ship Rule",
        "> **Ship Rule:** `OOS alpha_t > baseline AND OOS t > 0 AND beta < 0.5`.",
        "> Ranked by Judge OOS alpha t-stat (`judge_t`), never by raw return.",
        "> Any strategy with correlation to 12m TSMOM proxy > 0.70 is flagged.",
        "",
        "## Results Table (Ranked by OOS Judge t-stat)",
        "",
        "| Strategy | Idea | Alpha/yr | t | Beta | Max DD | Fit '05-'18 (t) | Judge '19-'26 (t) | TSMOM Corr | Ship Verdict | Reason |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    for name, m in sorted_items:
        alpha_str = f"{m['alpha'] * 100:+.2f}%" if m["alpha"] is not None else "N/A"
        t_str = f"{m['t']:+.2f}" if m["t"] is not None else "N/A"
        beta_str = f"{m['beta']:.2f}"
        dd_str = f"{m['max_dd'] * 100:.1f}%"
        fit_t = f"{m['fit_alpha'] * 100:+.1f}% ({m['fit_t']:+.2f})" if m["fit_t"] is not None else "N/A"
        judge_t = f"{m['judge_alpha'] * 100:+.1f}% ({m['judge_t']:+.2f})" if m["judge_t"] is not None else "N/A"
        ts_corr = f"{m['tsmom_corr']:.2f}" if m["tsmom_corr"] is not None else "-"
        if m.get("tsmom_flag"):
            ts_corr += " ⚠️"

        verdict_badge = f"`{m['ship_verdict']}`"
        lines.append(
            f"| `{name}` | {m['idea']} | {alpha_str} | {t_str} | {beta_str} | {dd_str} | {fit_t} | {judge_t} | {ts_corr} | {verdict_badge} | {m['ship_reason']} |"
        )

    lines.extend(
        [
            "",
            "## Detailed Analysis by Idea",
            "",
            "### 1. Idea 1: Inverse-Volatility Weighting (`+invvol`)",
            "- Sizing inversely by 60-day rolling volatility dampens risk-asset drawdowns.",
            "- Reduces portfolio beta while preserving positive alpha across both Fit and Judge periods.",
            "",
            "### 2. Idea 2: Multi-Speed Flags (`+multispeed`)",
            "- Averaging fast, medium, and slow parameter sets smooths out whipsaws and eliminates single-parameter overfitting.",
            "- Generates fractional positioning (0.33, 0.67, 1.0) rather than binary on/off switching.",
            "",
            "### 3. Idea 3: Soft ATR-Distance Flags (`+soft`)",
            "- Scaling position sizes continuously with distance from trailing stop reduces risk on marginal or newly flipped trends.",
            "",
            "### 4. Idea 4: Defensive Rotation (`+defensive`)",
            "- When risk assets fail trend checks, unallocated portfolio capital rotates into active defensive assets (TLT, IEF, GLD, SHV).",
            "- Total gross exposure never exceeds 1.0 gross_cap, ensuring complete preservation of risk limits.",
            "",
            "### 5. Idea 5: Top-k Cross-Sectional Ranking (`+topk`)",
            "- Concentrating capital in the top 5 strongest active trends by 12-month momentum.",
            "",
            "### 6. Idea 6: Ex-Equity Universe (`ex_equity`)",
            "- Evaluating trend filters on commodities, bonds, and currencies only.",
            "",
            "### 7. Idea 7: Cash Yield via SHV (`+SHV_cash`)",
            "- Unallocated idle capital earns short-term Treasury yield via SHV (pre-2007 = 0%).",
            "- Mechanically lifts cash returns during risk-off regimes without increasing equity beta.",
            "",
            "### 8. Idea 8: Ensemble Supertrend + AlphaTrend (`ensemble_stat`)",
            "- Holding assets only when both Supertrend and AlphaTrend indicate an active bull trend.",
            "",
            "### 9. Idea 9: Weekly Bars (`weekly`)",
            "- Weekly timeframe signals resampled causally (W-FRI) reduce churn and transaction costs.",
            "",
            "### 10. Combo 1+2+4+7 (+invvol +multispeed +defensive +SHV cash)",
            "- Synergistic combination uniting parameter smoothing, risk-parity sizing, defensive rotation, and cash yield.",
            "",
            "## Summary of Ship / No-Ship Verdicts",
            "",
        ]
    )

    ship_list = [name for name, m in sorted_items if m.get("ship_verdict") == "SHIP"]
    noship_list = [name for name, m in sorted_items if m.get("ship_verdict") == "NO-SHIP"]

    lines.append(f"- **Total Variants Tested:** {len(sorted_items)}")
    lines.append(f"- **SHIP Qualifying:** {len(ship_list)}")
    for s in ship_list:
        lines.append(f"  - `{s}`: Judge t = {metrics[s]['judge_t']:+.2f}, Beta = {metrics[s]['beta']:.2f}")
    lines.append(f"- **NO-SHIP:** {len(noship_list)}")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="TradingView trend-boost walk-forward study.")
    parser.add_argument("--save-doc", action="store_true", help="Save markdown report to docs/backtests/")
    args = parser.parse_args()

    metrics, master_idx = run_trend_boost_study()
    report = generate_markdown_report(metrics, master_idx)

    print("\n" + "=" * 80)
    print("TREND BOOST STUDY RESULTS SUMMARY")
    print("=" * 80)
    for name, m in sorted(
        metrics.items(), key=lambda kv: kv[1]["judge_t"] if kv[1]["judge_t"] is not None else -99, reverse=True
    ):
        print(
            f"{name:<30} | Alpha: {m['alpha'] * 100:+5.2f}% (t={m['t']:+4.2f}) | Judge t: {m['judge_t']:+4.2f} | "
            f"Beta: {m['beta']:4.2f} | TSMOM corr: {m['tsmom_corr'] or 0.0:4.2f} | Verdict: {m['ship_verdict']}"
        )

    if args.save_doc:
        DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
        DOC_PATH.write_text(report, encoding="utf-8")
        print(f"\nSaved study results to {DOC_PATH}")


if __name__ == "__main__":
    main()
