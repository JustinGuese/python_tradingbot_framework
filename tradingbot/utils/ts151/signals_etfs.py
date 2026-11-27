"""Pure signal functions for ETF trading strategies (Kakushadze & Serur 2018 §4).

Implements strategies 4.1 through 4.6:
- 4.1 sector_momentum: Sector momentum over window=252, skip=21, top k=3.
- 4.2 alpha_rotation: Jensen alpha vs benchmark over window=252, top k=3.
- 4.3 rsquared_rotation: Lowest R^2 vs benchmark (frac=0.5), top k=3 by alpha.
- 4.4 ibs_reversal: Internal Bar Strength mean-reversion with hysteresis (low=0.2, high=0.8).
- 4.5 leveraged_pair_rebalance: Short bull/bear pairs, rebalance on monthly roll or drift > band.
- 4.6 multiasset_trend: Positive trailing return filter + inverse-volatility weighting.

No database and no network calls. Operates purely on Ctx and price data.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

from tradingbot.utils.ts151 import universes
from tradingbot.utils.ts151.base import Ctx
from tradingbot.utils.ts151.common import (
    ibs as calc_ibs,
    inverse_vol,
    rebalance_due,
    replay_state,
    topk_bottomk,
    trailing_return,
)


def sector_momentum(
    ctx: Ctx,
    window: int = 252,
    skip: int = 21,
    k: int = 3,
    gross: float = 1.0,
) -> dict[str, float] | None:
    """Strategy 4.1: Sector Momentum.

    Ranks sector ETFs by trailing return over `window` bars ending `skip` bars ago.
    Holds the top `k` names in equal weight.

    Returns None if history is shorter than `window + skip + 1` or fewer than `k`
    assets have valid returns.
    """
    needed_bars = window + skip + 1
    if len(ctx.closes) < needed_bars:
        return None

    scores: dict[str, float] = {}
    for sym in ctx.universe:
        if sym in ctx.closes.columns:
            series = ctx.closes[sym]
            ret = trailing_return(series, window=window, skip=skip)
            if isinstance(ret, float) and np.isfinite(ret):
                scores[sym] = ret

    if len(scores) < k:
        return None

    weights = topk_bottomk(scores, k=k, long_only=True, gross=gross)
    return weights if weights else None


def alpha_rotation(
    ctx: Ctx,
    window: int = 252,
    k: int = 3,
    benchmark: str = "SPY",
    gross: float = 1.0,
) -> dict[str, float] | None:
    """Strategy 4.2: Alpha Rotation.

    Ranks sector ETFs by Jensen's alpha against `benchmark` (default SPY) over
    the trailing `window` return bars. Holds the top `k` names in equal weight.

    Jensen's alpha is estimated via OLS on daily returns:
        R_i = alpha_i + beta_i * R_m + eps_i
        alpha_i = mean(R_i) - beta_i * mean(R_m)

    Returns None if benchmark is missing, history is shorter than `window + 1`,
    or fewer than `k` assets have valid alpha estimates.
    """
    if benchmark not in ctx.closes.columns or len(ctx.closes) < window + 1:
        return None

    returns = ctx.closes.pct_change().iloc[-window:]
    r_m = returns[benchmark].dropna()
    if len(r_m) < window:
        return None

    var_m = float(r_m.var())
    if var_m == 0.0 or math.isnan(var_m):
        return None

    mean_m = float(r_m.mean())
    alphas: dict[str, float] = {}

    for sym in ctx.universe:
        if sym in returns.columns and sym != benchmark:
            r_i = returns[sym].dropna()
            if len(r_i) < window:
                continue
            aligned = pd.concat([r_i, r_m], axis=1).dropna()
            if len(aligned) < window:
                continue
            cov = float(aligned.iloc[:, 0].cov(aligned.iloc[:, 1]))
            beta = cov / var_m
            alpha = float(aligned.iloc[:, 0].mean()) - beta * mean_m
            if np.isfinite(alpha):
                alphas[sym] = alpha

    if len(alphas) < k:
        return None

    weights = topk_bottomk(alphas, k=k, long_only=True, gross=gross)
    return weights if weights else None


def rsquared_rotation(
    ctx: Ctx,
    window: int = 126,
    frac: float = 0.5,
    k: int = 3,
    benchmark: str = "SPY",
    gross: float = 1.0,
) -> dict[str, float] | None:
    """Strategy 4.3: R-squared Rotation.

    Filters sector ETFs to the fraction `frac` with the lowest R^2 against `benchmark`
    over the trailing `window` return bars. Among those candidates, selects the top `k`
    by Jensen's alpha, weighted equally.

    Returns None if benchmark is missing, history is shorter than `window + 1`,
    or fewer than `k` candidates remain.
    """
    if benchmark not in ctx.closes.columns or len(ctx.closes) < window + 1:
        return None

    returns = ctx.closes.pct_change().iloc[-window:]
    r_m = returns[benchmark].dropna()
    if len(r_m) < window:
        return None

    var_m = float(r_m.var())
    if var_m == 0.0 or math.isnan(var_m):
        return None

    mean_m = float(r_m.mean())
    stats: dict[str, tuple[float, float]] = {}  # sym -> (r_squared, alpha)

    for sym in ctx.universe:
        if sym in returns.columns and sym != benchmark:
            r_i = returns[sym].dropna()
            if len(r_i) < window:
                continue
            aligned = pd.concat([r_i, r_m], axis=1).dropna()
            if len(aligned) < window:
                continue
            s_i = aligned.iloc[:, 0]
            s_m = aligned.iloc[:, 1]

            corr = float(s_i.corr(s_m))
            if not np.isfinite(corr):
                continue
            r2 = corr * corr

            cov = float(s_i.cov(s_m))
            beta = cov / var_m
            alpha = float(s_i.mean()) - beta * mean_m
            if np.isfinite(alpha) and np.isfinite(r2):
                stats[sym] = (r2, alpha)

    if len(stats) < k:
        return None

    # Step 1: select frac lowest R^2
    n_keep = max(k, int(round(len(stats) * frac)))
    sorted_by_r2 = sorted(stats.items(), key=lambda item: item[1][0])
    candidates = dict(sorted_by_r2[:n_keep])

    if len(candidates) < k:
        return None

    # Step 2: rank candidates by alpha descending
    alphas = {sym: alpha for sym, (_, alpha) in candidates.items()}
    weights = topk_bottomk(alphas, k=k, long_only=True, gross=gross)
    return weights if weights else None


def ibs_reversal(
    ctx: Ctx,
    low: float = 0.2,
    high: float = 0.8,
    gross: float = 1.0,
) -> dict[str, float] | None:
    """Strategy 4.4: Internal Bar Strength (IBS) Mean-Reversion.

    For each ETF in universe:
      IBS = (close - low) / (high - low)
      enter long when IBS < `low` (default 0.2)
      exit to flat when IBS > `high` (default 0.8)
      otherwise hold state

    Position state is replayed from inception using `replay_state`.
    Equal weight is allocated across held ETFs. Returns {} if no ETFs are held,
    or None if no bar data is available.
    """
    if not ctx.universe:
        return None

    held: list[str] = []
    valid_any = False

    for sym in ctx.universe:
        df = ctx.datas.get(sym)
        if df is None or df.empty:
            continue

        cols = {c.lower(): c for c in df.columns}
        if not ("high" in cols and "low" in cols and "close" in cols):
            continue

        valid_any = True
        h = df[cols["high"]].astype(float)
        l = df[cols["low"]].astype(float)
        c = df[cols["close"]].astype(float)

        hl = h - l
        denom = hl.replace(0.0, np.nan)
        ibs_series = (c - l) / denom
        ibs_series = ibs_series.fillna(0.5)

        long_enter = ibs_series < low
        long_exit = ibs_series > high

        state = replay_state(long_enter, long_exit)
        if state == 1:
            held.append(sym)

    if not valid_any:
        return None

    if not held:
        return {}

    w = gross / len(held)
    return {sym: float(w) for sym in held}


def leveraged_pair_rebalance(
    ctx: Ctx,
    pairs: Sequence[tuple[str, str]] | None = None,
    band: float = 0.1,
    gross: float = 1.0,
) -> dict[str, float] | None:
    """Strategy 4.5: Leveraged ETF Pairs.

    Shorts each bull/bear pair in equal dollars (total portfolio gross = `gross`).
    Rebalances back to equal weights when the drift of either leg within any pair
    exceeds `band` (default 0.1, i.e. leg share outside [0.4, 0.6]), or on a calendar
    month roll. Otherwise holds position (returns None).

    Drift is evaluated purely through price replay across available history so that
    backtest and live state behave identically without referencing external state.
    """
    if pairs is None:
        # Match from LEVERAGED_PAIRS or pair consecutive universe members
        active_pairs = [
            p
            for p in universes.LEVERAGED_PAIRS
            if p[0] in ctx.closes.columns and p[1] in ctx.closes.columns
        ]
        if not active_pairs and len(ctx.universe) >= 2:
            u = [s for s in ctx.universe if s in ctx.closes.columns]
            active_pairs = [(u[i], u[i + 1]) for i in range(0, len(u) - 1, 2)]
    else:
        active_pairs = [
            p for p in pairs if p[0] in ctx.closes.columns and p[1] in ctx.closes.columns
        ]

    if not active_pairs or ctx.closes.empty:
        return None

    all_symbols = [s for pair in active_pairs for s in pair]
    closes_subset = ctx.closes[all_symbols].dropna()
    if closes_subset.empty:
        return None

    num_bars = len(closes_subset)
    t_last = 0  # index of last rebalance bar within closes_subset

    for t in range(1, num_bars):
        # Condition 1: Monthly rebalance due
        idx_slice = closes_subset.index[: t + 1]
        m_due = rebalance_due(idx_slice, "M")

        # Condition 2: Check leg drift within each pair
        drifted = False
        if not m_due:
            for s1, s2 in active_pairs:
                p1_0 = float(closes_subset[s1].iloc[t_last])
                p2_0 = float(closes_subset[s2].iloc[t_last])
                if p1_0 <= 0 or p2_0 <= 0:
                    continue
                v1 = float(closes_subset[s1].iloc[t]) / p1_0
                v2 = float(closes_subset[s2].iloc[t]) / p2_0
                total_v = v1 + v2
                if total_v <= 0:
                    continue
                w1 = v1 / total_v
                if abs(w1 - 0.5) > band:
                    drifted = True
                    break

        if m_due or drifted:
            t_last = t

    # Check if current bar triggered a rebalance
    is_rebalance_bar = (t_last == num_bars - 1)
    if not is_rebalance_bar:
        return None

    # Target short weights: equal dollar short across all legs
    num_legs = len(active_pairs) * 2
    w_each = -gross / num_legs
    return {sym: float(w_each) for sym in all_symbols}


def multiasset_trend(
    ctx: Ctx,
    window: int = 252,
    vol_window: int = 63,
    gross: float = 1.0,
) -> dict[str, float] | None:
    """Strategy 4.6: Multi-asset Trend.

    Evaluates assets in universe for positive trailing return over `window` bars.
    Assets with return > 0 are weighted inversely proportional to realized volatility
    over `vol_window` bars, scaled to total weight `gross`.

    Returns {} if no assets qualify (flat/cash), or None if insufficient history.
    """
    needed_bars = max(window, vol_window) + 1
    if len(ctx.closes) < needed_bars:
        return None

    qualifying: list[str] = []
    for sym in ctx.universe:
        if sym in ctx.closes.columns:
            ret = trailing_return(ctx.closes[sym], window=window, skip=0)
            if isinstance(ret, float) and np.isfinite(ret) and ret > 0.0:
                qualifying.append(sym)

    if not qualifying:
        return {}

    sub_closes = ctx.closes[qualifying]
    inv_w = inverse_vol(sub_closes, window=vol_window)
    if inv_w.empty:
        return {}

    w_scaled = (inv_w / inv_w.sum()) * gross
    return {str(k): float(v) for k, v in w_scaled.items() if np.isfinite(v)}
