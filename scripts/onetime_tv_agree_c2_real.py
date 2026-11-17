"""Idea 6 redo: agreement filter on the TSMOM sleeve, blended with the REAL C2 sleeves.

The first pass (onetime_tv_agree_c2.py) used equal-weight proxies for the TARegimeMultiAsset and
GoldenButterfly sleeves. Here those two are the live bots' own backtest returns
(`scripts.onetime_c2_blend_backtest.sleeve_returns`), and the TSMOM sleeve is a vectorised replica of
TSMOMTrendBot (12m sign rule, 60d inverse-vol, target 10%, leg cap 15%, gross <= 1), validated against the real bot
sleeve before any filter is applied.

Pre-registered variants (n=3): tsmom_base, tsmom_and_st, tsmom_and_st_at. A filter masks a TSMOM leg to cash when
Supertrend (and AlphaTrend) are not long; remaining legs keep their inverse-vol weights (no renormalisation).
Blend weights are the existing fixed C2 weights 0.39 / 0.37 / 0.24 (not refit). The 2024+ holdout is never touched.

Usage: POSTGRES_URI=postgresql://ci:ci@localhost:5432/ci .venv/bin/python scripts/onetime_tv_agree_c2_real.py --save-doc
"""

# ruff: noqa: E402
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.onetime_c2_blend_backtest as c2
from scripts.onetime_tv_daily_study import (
    CACHE_DIR,
    compute_flags_matrix,
    compute_strategy_returns,
    load_daily_data,
)
from scripts.onetime_tv_widen_study import HOLDOUT_START, alpha_stats, split_periods
from tradingbot.goldenbutterflymombot import GoldenButterflyMomBot
from tradingbot.taregimemultiassetbot import TARegimeMultiAssetBot
from tradingbot.tsmomtrendbot import UNIVERSE as TSMOM_UNIVERSE
from tradingbot.tsmomtrendbot import TSMOMTrendBot

DOC_PATH = Path("docs/backtests/tv-agree-c2-real-2026-11.md")
VARIANTS = ["tsmom_base", "tsmom_and_st", "tsmom_and_st_at"]
W_TA, W_GB, W_TSMOM = 0.39, 0.37, 0.24
LOOKBACK, VOL_WINDOW, TARGET_VOL, MAX_LEG = 252, 60, 0.10, 0.15


def _norm(s: pd.Series) -> pd.Series:
    out = s.copy()
    out.index = pd.DatetimeIndex(out.index).tz_localize(None).normalize()
    return out[~out.index.duplicated(keep="last")]


def tsmom_weights(closes: pd.DataFrame, mask: pd.DataFrame | None = None) -> pd.DataFrame:
    """Vectorised TSMOMTrendBot weights: positive 12m momentum, inverse-vol sized, capped, gross <= 1."""
    rets = closes.pct_change()
    vol = rets.rolling(VOL_WINDOW).std() * np.sqrt(252)
    mom = closes / closes.shift(LOOKBACK) - 1.0
    ok = (mom > 0) & (vol > 0)
    if mask is not None:
        ok &= mask.reindex(columns=closes.columns).fillna(False).astype(bool)
    raw = (TARGET_VOL / vol).clip(upper=MAX_LEG).where(ok, 0.0).fillna(0.0)
    gross = raw.sum(axis=1)
    scale = np.where(gross > 1.0, 1.0 / gross.replace(0.0, np.nan), 1.0)
    return raw.mul(pd.Series(scale, index=raw.index).fillna(1.0), axis=0)


def build_variants() -> tuple[dict[str, pd.Series], pd.Series]:
    dfs, idx, _, _ = load_daily_data(cache_dir=CACHE_DIR, universe=list(TSMOM_UNIVERSE))
    closes = pd.DataFrame({t: dfs[t]["close"] for t in TSMOM_UNIVERSE if t in dfs}).reindex(idx).ffill()
    st = compute_flags_matrix(dfs, idx, kind="supertrend", universe=list(TSMOM_UNIVERSE))
    at = compute_flags_matrix(dfs, idx, kind="alphatrend", universe=list(TSMOM_UNIVERSE))
    masks = {"tsmom_base": None, "tsmom_and_st": st, "tsmom_and_st_at": st & at}
    rets = {v: _norm(compute_strategy_returns(tsmom_weights(closes, m), closes)) for v, m in masks.items()}
    qqq = _norm(dfs["QQQ"]["close"].reindex(idx).ffill().pct_change())
    return rets, qqq


def fmt(stats: dict) -> dict:
    return {
        "alpha": f"{stats['alpha']:+.2%}",
        "t": round(stats["t"], 2),
        "t_adj": round(stats["t_adj"], 2),
        "beta": round(stats["beta"], 2),
        "max_dd": f"{stats['max_dd']:.1%}",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save-doc", action="store_true")
    args = ap.parse_args()
    cutoff = pd.Timestamp(HOLDOUT_START)

    sleeves, qqq = build_variants()
    sleeves = {k: v[v.index < cutoff] for k, v in sleeves.items()}
    qqq = qqq[qqq.index < cutoff]

    # Real live-bot sleeves (offline sqlite + yfinance, from 2017-06).
    real = {
        "ta": _norm(c2.sleeve_returns(TARegimeMultiAssetBot)),
        "gb": _norm(c2.sleeve_returns(GoldenButterflyMomBot)),
        "tsmom_real": _norm(c2.sleeve_returns(TSMOMTrendBot)),
    }
    real = {k: v[v.index < cutoff] for k, v in real.items()}

    # 1) Replica check: vectorised base vs the real TSMOMTrendBot sleeve.
    chk = pd.concat({"vec": sleeves["tsmom_base"], "real": real["tsmom_real"]}, axis=1, join="inner").dropna()
    corr_check = float(chk["vec"].corr(chk["real"]))
    print(f"replica check: corr(vec base, real TSMOM bot) = {corr_check:.3f} over {len(chk)} days")

    # 2) Sleeve-level on full history (Fit / Validate; holdout excluded).
    rows = []
    periods = split_periods(sleeves["tsmom_base"].index)
    for v in VARIANTS:
        for p in ("fit", "validate"):
            sub = sleeves[v].reindex(periods[p]).dropna()
            s = alpha_stats(sub, qqq.reindex(sub.index), n_variants=len(VARIANTS))
            rows.append({"level": "sleeve", "variant": v, "period": p, **fmt(s)})

    # 3) Blend with REAL TA and GB sleeves, fixed C2 weights; window 2017-06 .. 2023-12, split at midpoint.
    common = pd.concat({"ta": real["ta"], "gb": real["gb"]}, axis=1, join="inner").dropna()
    blends = {}
    for v in VARIANTS:
        j = pd.concat({"ta": common["ta"], "gb": common["gb"], "ts": sleeves[v]}, axis=1, join="inner").dropna()
        blends[v] = W_TA * j["ta"] + W_GB * j["gb"] + W_TSMOM * j["ts"]
    j = pd.concat({"ta": common["ta"], "gb": common["gb"], "ts": real["tsmom_real"]}, axis=1, join="inner").dropna()
    blends["real_tsmom_blend(ref)"] = W_TA * j["ta"] + W_GB * j["gb"] + W_TSMOM * j["ts"]
    for name, b in blends.items():
        mid = len(b) // 2
        for lab, sub in (("H1", b.iloc[:mid]), ("H2", b.iloc[mid:]), ("full", b)):
            s = alpha_stats(sub, qqq.reindex(sub.index), n_variants=len(VARIANTS))
            rows.append(
                {
                    "level": "blend",
                    "variant": name,
                    "period": f"{lab} {sub.index[0].date()}..{sub.index[-1].date()}",
                    **fmt(s),
                }
            )

    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    if args.save_doc:
        DOC_PATH.write_text(
            "# TSMOM agreement filter with real C2 sleeves (idea 6 redo)\n\n"
            f"Replica check: corr(vectorised TSMOM base, real TSMOMTrendBot sleeve) = {corr_check:.3f}.\n"
            "Blend = 0.39 real TARegimeMultiAsset + 0.37 real GoldenButterfly + 0.24 TSMOM variant. "
            "Holdout (2024+) untouched; n_variants=3 for t_adj.\n\n" + df.to_markdown(index=False) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {DOC_PATH}")


if __name__ == "__main__":
    main()
