"""
Cross-sectional ranking filters and structure variants for CrossVol.

Provides candidate ranking methods:
1. gap: implied vol minus Yang-Zhang HAR forecast fair vol (IV - fair, existing baseline).
2. vol_of_vol: sample standard deviation of daily ATM IV changes.
3. idio_vol: annualized sample standard deviation of residuals from stock returns
   regressed on market benchmark (QQQ/SPY) returns.

Pure functions that drop names with insufficient history and sort candidates deterministically.
Also provides custom decision generators for option replay backtesting with ranking
and delta-hedged call writing variants (Han et al.).
"""

from __future__ import annotations

import logging
import math
from typing import Callable, Sequence

import numpy as np
import pandas as pd

from tradingbot.utils import mispricing_scan as ms
from tradingbot.utils import options
from tradingbot.utils.option_decide import Action, Close, Hedge, Holdings, Market, Open
from tradingbot.utils.option_rules import CrossVolRules, NameVol, cross_vol_exit_reason
from tradingbot.utils.option_strategies import DIVIDEND_REASON, _threatened
from tradingbot.utils.vol_indices import VIX

logger = logging.getLogger(__name__)


def calc_gap(iv: float | None, fair: float | None) -> float | None:
    """Implied vol minus forecast fair vol."""
    if iv is None or fair is None or math.isnan(iv) or math.isnan(fair):
        return None
    return float(iv - fair)


def calc_vol_of_vol(
    iv_history: pd.Series | Sequence[float],
    min_history: int = 20,
) -> float | None:
    """
    Standard deviation of daily ATM IV changes: std(IV_t - IV_{t-1}).
    Returns None if fewer than `min_history` changes are available.
    """
    if isinstance(iv_history, pd.Series):
        s = iv_history.dropna()
    else:
        s = pd.Series([x for x in iv_history if x is not None and not math.isnan(x)])
    if len(s) < min_history + 1:
        return None
    diffs = s.diff().dropna()
    if len(diffs) < min_history:
        return None
    val = float(diffs.std(ddof=1))
    return val if not math.isnan(val) else None


def calc_idiosyncratic_vol(
    stock_returns: pd.Series | Sequence[float],
    market_returns: pd.Series | Sequence[float],
    min_history: int = 20,
) -> float | None:
    """
    Annualized standard deviation of residuals from OLS of stock returns on market returns:
    R_i = alpha + beta * R_m + eps.
    Returns None if fewer than `min_history` aligned observations are available.
    """
    if isinstance(stock_returns, pd.Series) and isinstance(market_returns, pd.Series):
        df = pd.concat([stock_returns.rename("stock"), market_returns.rename("market")], axis=1).dropna()
        if len(df) < min_history:
            return None
        r_i = df["stock"].to_numpy(dtype=float)
        r_m = df["market"].to_numpy(dtype=float)
    else:
        s_arr = [x for x in stock_returns if x is not None and not math.isnan(x)]
        m_arr = [x for x in market_returns if x is not None and not math.isnan(x)]
        min_len = min(len(s_arr), len(m_arr))
        if min_len < min_history:
            return None
        r_i = np.array(s_arr[-min_len:], dtype=float)
        r_m = np.array(m_arr[-min_len:], dtype=float)

    var_m = float(np.var(r_m, ddof=1))
    if var_m <= 1e-12:
        return None
    cov_im = float(np.cov(r_i, r_m, ddof=1)[0, 1])
    beta = cov_im / var_m
    alpha = float(np.mean(r_i) - beta * np.mean(r_m))
    residuals = r_i - (alpha + beta * r_m)
    res_std = float(np.std(residuals, ddof=1))
    if math.isnan(res_std):
        return None
    return float(res_std * math.sqrt(252))


def rank_name_vols(
    names: Sequence[NameVol],
    rank_metric: str | None = None,
    *,
    iv_histories: dict[str, pd.Series | Sequence[float]] | None = None,
    stock_returns: dict[str, pd.Series | Sequence[float]] | None = None,
    market_returns: pd.Series | Sequence[float] | None = None,
    min_history: int = 20,
    reverse: bool = True,
) -> list[tuple[NameVol, float]]:
    """
    Rank NameVol candidates deterministically by rank_metric ('gap', 'vol_of_vol', 'idio_vol').
    Drops candidates that have insufficient history or missing metric.
    Ties broken deterministically by underlying ticker symbol.
    """
    metric = rank_metric or "gap"
    scored: list[tuple[NameVol, float]] = []

    for nv in names:
        u = nv.underlying
        val: float | None = None
        if metric == "gap":
            val = nv.gap
        elif metric == "vol_of_vol":
            if iv_histories and u in iv_histories:
                val = calc_vol_of_vol(iv_histories[u], min_history=min_history)
        elif metric == "idio_vol":
            if stock_returns and market_returns is not None and u in stock_returns:
                val = calc_idiosyncratic_vol(stock_returns[u], market_returns, min_history=min_history)
        else:
            raise ValueError(f"Unknown rank_metric: {metric}")

        if val is not None and not math.isnan(val):
            scored.append((nv, float(val)))

    # Deterministic sort: score primary, ticker symbol secondary
    scored.sort(key=lambda item: (-item[1] if reverse else item[1], item[0].underlying))
    return scored


def select_candidates_ranked(
    names: Sequence[NameVol],
    held: set[str],
    rules: CrossVolRules,
    *,
    iv_histories: dict[str, pd.Series | Sequence[float]] | None = None,
    stock_returns: dict[str, pd.Series | Sequence[float]] | None = None,
    market_returns: pd.Series | Sequence[float] | None = None,
    min_history: int = 20,
) -> list[NameVol]:
    """
    Select up to (max_positions - len(held)) candidates.
    Filters: not in held, earnings_clear, gap >= rules.min_gap.
    Ranked according to rules.rank_metric (defaults to gap). Drops names with too little history.
    """
    ok = [
        n
        for n in names
        if n.underlying not in held and n.earnings_clear and n.gap is not None and n.gap >= rules.min_gap
    ]
    ranked = rank_name_vols(
        ok,
        rank_metric=rules.rank_metric,
        iv_histories=iv_histories,
        stock_returns=stock_returns,
        market_returns=market_returns,
        min_history=min_history,
    )
    needed = max(rules.max_positions - len(held), 0)
    return [nv for nv, _ in ranked[:needed]]


def make_crossvol_decide(
    rules: CrossVolRules,
    universe: Sequence[str],
    min_history: int = 20,
    benchmark_symbol: str = "QQQ",
) -> Callable[[Market, Holdings], list[Action]]:
    """
    Factory for a decide(market, holdings) function compatible with run_strategy.
    Maintains rolling daily ATM IV and price histories for vol-of-vol and idiosyncratic vol ranking.
    Supports delta-hedged call writing (Han et al.) when rules.structure == 'delta_hedged_call'.
    """
    iv_history: dict[str, list[float]] = {u: [] for u in universe}

    def decide(market: Market, holdings: Holdings) -> list[Action]:
        actions: list[Action] = []
        is_hedged_call = rules.structure in ("call", "delta_hedged_call")

        # 1. Check exit and hedging conditions for held positions
        for u in sorted(holdings.underlyings()):
            book = holdings.book(u)
            if book.empty:
                continue
            reason = cross_vol_exit_reason(book.credit, book.pnl, book.dte, rules)
            if not reason and _threatened(market, book):
                reason = DIVIDEND_REASON
            if reason:
                actions.append(Close(u, include_stock=True, reason=reason))
            elif is_hedged_call:
                # Maintain delta hedge for open position
                actions.append(Hedge(u, 0.0))

        closed = {a.underlying for a in actions if isinstance(a, Close)}
        held = holdings.underlyings() - closed
        if len(held) >= rules.max_positions:
            return actions

        vix = market.vol_index(VIX)
        if vix is None or vix >= rules.max_vix:
            return actions

        # 2. Shortlist and inspect live chains
        names = ms.shortlist(universe, market.scan_scores(), rules.shortlist)
        ohlc = market.ohlc(names)
        scanned: list[NameVol] = []
        views: dict[str, options.ChainView] = {}

        for u in names:
            if u in held:
                continue
            nv, view = market.name_vol(u, rules.target_dte, ohlc.get(u))
            if view is None or not view.live:
                continue
            if nv.iv is not None and not math.isnan(nv.iv):
                if u not in iv_history:
                    iv_history[u] = []
                iv_history[u].append(float(nv.iv))
            scanned.append(nv)
            views[u] = view

        if not views:
            return actions

        # 3. Compute benchmark and stock return series for idiosyncratic vol
        stock_rets: dict[str, pd.Series] = {}
        mkt_rets: pd.Series | None = None
        if rules.rank_metric == "idio_vol":
            bench_part = market.ohlc([benchmark_symbol]).get(benchmark_symbol)
            if bench_part is not None and len(bench_part) >= 2:
                mkt_rets = bench_part["close"].pct_change().dropna()
            for u in names:
                u_df = ohlc.get(u)
                if u_df is not None and len(u_df) >= 2:
                    stock_rets[u] = u_df["close"].pct_change().dropna()

        # 4. Rank candidates and drop names with insufficient history
        candidates = select_candidates_ranked(
            scanned,
            held,
            rules,
            iv_histories=iv_history,
            stock_returns=stock_rets,
            market_returns=mkt_rets,
            min_history=min_history,
        )

        budget = rules.risk_per_name_pct * holdings.equity()
        for nv in candidates:
            view = views[nv.underlying]
            meta = {"iv": nv.iv, "fair": nv.fair, "z": nv.z}
            try:
                if is_hedged_call:
                    # Delta-hedged call spread (bear call spread + delta hedge shares)
                    pick = options.select_vertical(
                        nv.underlying,
                        "C",
                        rules.short_delta,
                        rules.width_pct * view.spot,
                        rules.target_dte,
                        view=view,
                    )
                    actions.append(Open(pick, budget, reason=f"call gap {nv.gap * 100:+.1f} pts", meta=meta))
                    actions.append(Hedge(nv.underlying, 0.0))
                else:
                    # Default iron condor
                    pick = options.select_iron_condor(
                        nv.underlying,
                        rules.short_delta,
                        rules.width_pct * view.spot,
                        rules.target_dte,
                        view=view,
                    )
                    actions.append(Open(pick, budget, reason=f"gap {nv.gap * 100:+.1f} pts", meta=meta))
            except ValueError as exc:
                logger.warning("%s: failed to select structure (%s)", nv.underlying, exc)
                continue

        return actions

    return decide
