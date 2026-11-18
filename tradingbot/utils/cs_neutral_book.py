"""Cross-sectional market-neutral stock book engine (F1).

Pure functions, no database access.
Implements:
1. True residual momentum (12-1 return of residuals from a 36-month regression
   on the market, distinct from the demeaning in numerai_features.py).
2. 1-month short-term reversal (negative trailing 1-month return).
3. Within-sector z-scoring using sector classifications (e.g. SP500_SECTOR).
4. Composite signal generation blending residual momentum and reversal.
5. Dollar-neutral portfolio construction: top-k long / bottom-k short at equal
   weight with long = short = 0.9 (Reg T headroom: gross 1.8 <= 2.0),
   strictly enforcing equal counts per sector side.
6. Optional beta scaling / hedging to benchmark (e.g. QQQ).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Literal

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def compute_residual_momentum(
    prices: pd.DataFrame,
    market_prices: pd.Series | str | None = None,
    *,
    regression_lookback: int = 756,
    momentum_bars: int = 252,
    skip_bars: int = 21,
    min_history: int = 252,
    standardize: bool = False,
) -> pd.Series:
    """Calculate true residual momentum as the 12-1 return of residuals from a

    36-month (default 756 trading days) OLS regression of stock returns on market returns.

    For each stock i:
        R_{i, tau} = alpha_i + beta_i * R_{m, tau} + eps_{i, tau}
    estimated over the trailing regression_lookback window ending at bar t.
    The residual momentum is the cumulative sum of residuals eps_{i, tau} over the
    12-1 momentum window [t - momentum_bars + 1, t - skip_bars].

    If standardize=True, residual momentum is divided by the standard deviation of
    the regression residuals sigma(eps_i) over the estimation window (Blitz, Huij &
    Martens 2011).

    Parameters
    ----------
    prices : pd.DataFrame
        Historical price series with dates as index and tickers as columns.
    market_prices : pd.Series | str | None, optional
        Market benchmark prices. If None, looks for 'SPY' in prices.columns,
        falling back to the cross-sectional mean price. If str, uses prices[market_prices].
    regression_lookback : int, default 756
        Lookback window in trading bars for the regression (36 months ~ 756 daily bars).
    momentum_bars : int, default 252
        Momentum formation period in trading bars (12 months ~ 252 daily bars).
    skip_bars : int, default 21
        Skip period in trading bars to avoid 1-month reversal (1 month ~ 21 daily bars).
    min_history : int, default 252
        Minimum number of valid aligned return bars required to run the regression.
    standardize : bool, default False
        Whether to divide the cumulative residual return by the residual standard deviation.

    Returns
    -------
    pd.Series
        Residual momentum score per stock as of the latest bar in prices.
    """
    if prices.empty:
        return pd.Series(dtype=float)

    if momentum_bars <= skip_bars:
        raise ValueError(f"momentum_bars ({momentum_bars}) must be strictly greater than skip_bars ({skip_bars})")
    if regression_lookback < momentum_bars:
        raise ValueError(f"regression_lookback ({regression_lookback}) must be >= momentum_bars ({momentum_bars})")

    # Resolve market series
    if isinstance(market_prices, str):
        if market_prices not in prices.columns:
            raise KeyError(f"Market symbol {market_prices!r} not in prices DataFrame columns")
        mkt_series = prices[market_prices]
        stock_df = prices.drop(columns=[market_prices]) if prices.shape[1] > 1 else prices
    elif isinstance(market_prices, pd.Series):
        mkt_series = market_prices
        stock_df = prices
    else:
        if "SPY" in prices.columns:
            mkt_series = prices["SPY"]
            stock_df = prices.drop(columns=["SPY"]) if prices.shape[1] > 1 else prices
        else:
            mkt_series = prices.mean(axis=1)
            stock_df = prices

    # Slice strictly up to latest bar for regression_lookback
    needed_bars = regression_lookback + 1
    recent_prices = stock_df.iloc[-needed_bars:]
    recent_mkt = mkt_series.reindex(recent_prices.index)

    # Compute daily percentage returns
    stock_rets = recent_prices.pct_change(fill_method=None).iloc[1:]
    mkt_rets = recent_mkt.pct_change(fill_method=None).iloc[1:]

    out = pd.Series(np.nan, index=stock_df.columns, dtype=float)

    # Momentum window within the return slice
    n_ret_bars = len(mkt_rets)
    if n_ret_bars < momentum_bars or n_ret_bars < min_history:
        return out

    # Determine indices for the 12-1 momentum window
    # In return bars: last bar is index -1 (bar t).
    # skip_bars means we stop at -1 - skip_bars.
    # momentum_bars means we start at -momentum_bars.
    mom_start_idx = -momentum_bars
    mom_end_idx = -skip_bars  # exclusive in python slice: slice(mom_start_idx, mom_end_idx)

    mkt_arr = mkt_rets.to_numpy(dtype=float)

    for col in stock_df.columns:
        s_arr = stock_rets[col].to_numpy(dtype=float)
        valid_mask = np.isfinite(s_arr) & np.isfinite(mkt_arr)
        if np.count_nonzero(valid_mask) < min_history:
            continue

        s_valid = s_arr[valid_mask]
        m_valid = mkt_arr[valid_mask]

        var_m = float(np.var(m_valid, ddof=1))
        if var_m <= 1e-12:
            continue

        cov_sm = float(np.cov(s_valid, m_valid, ddof=1)[0, 1])
        beta = cov_sm / var_m
        alpha = float(np.mean(s_valid) - beta * np.mean(m_valid))

        # Full residuals over the aligned window
        residuals_full = s_arr - (alpha + beta * mkt_arr)

        # Slice 12-1 window
        res_win = residuals_full[mom_start_idx:mom_end_idx]
        res_win_valid = res_win[np.isfinite(res_win)]

        if len(res_win_valid) == 0:
            continue

        cum_res = float(np.sum(res_win_valid))

        if standardize:
            res_std = float(np.nanstd(residuals_full, ddof=1))
            if res_std > 1e-12:
                out[col] = cum_res / res_std
            else:
                out[col] = 0.0
        else:
            out[col] = cum_res

    return out


def compute_short_term_reversal(
    prices: pd.DataFrame,
    lookback_bars: int = 21,
) -> pd.Series:
    """Calculate 1-month short-term reversal as the negative trailing return over lookback_bars.

    Stocks that dropped the most over trailing lookback_bars receive the highest reversal score
    (anticipating mean-reverting bounce).
    """
    if len(prices) <= lookback_bars:
        return pd.Series(np.nan, index=prices.columns, dtype=float)

    p_now = pd.to_numeric(prices.iloc[-1], errors="coerce")
    p_past = pd.to_numeric(prices.iloc[-1 - lookback_bars], errors="coerce")

    valid = (p_now > 0) & (p_past > 0) & p_now.notna() & p_past.notna()
    ret = pd.Series(np.nan, index=prices.columns, dtype=float)
    ret[valid] = (p_now[valid] / p_past[valid]) - 1.0

    return -ret


def within_sector_zscore(
    signal: pd.Series,
    sector_map: Mapping[str, str] | None = None,
    min_valid: int = 2,
) -> pd.Series:
    """Standardize a cross-sectional signal within sectors to mean 0 and standard deviation 1.

    If sector_map is None, attempts to load SP500_SECTOR from tradingbot.utils.universes.
    Tickers missing from sector_map are grouped into 'UNKNOWN'.
    """
    if signal.empty:
        return signal.copy()

    if sector_map is None:
        try:
            from tradingbot.utils.universes import SP500_SECTOR

            sector_map = SP500_SECTOR
        except ImportError:
            sector_map = {}

    out = pd.Series(np.nan, index=signal.index, dtype=float)
    s = pd.to_numeric(signal, errors="coerce")

    # Map tickers to sector
    sectors = pd.Series([sector_map.get(t, "UNKNOWN") for t in s.index], index=s.index)

    for _, group_idx in sectors.groupby(sectors).groups.items():
        sub = s.loc[group_idx]
        valid_mask = sub.notna() & np.isfinite(sub)
        valid_sub = sub[valid_mask]
        n_val = len(valid_sub)

        if n_val < min_valid:
            if n_val == 1:
                out.loc[valid_sub.index] = 0.0
            continue

        std = float(valid_sub.std(ddof=1))
        mean = float(valid_sub.mean())
        if std > 1e-12:
            out.loc[valid_sub.index] = (valid_sub - mean) / std
        else:
            out.loc[valid_sub.index] = 0.0

    return out


def compute_composite_signal(
    residual_mom: pd.Series,
    reversal: pd.Series,
    sector_map: Mapping[str, str] | None = None,
    *,
    weight_mom: float = 0.5,
    weight_rev: float = 0.5,
    zscore_inputs: bool = True,
    zscore_output: bool = True,
) -> pd.Series:
    """Combine residual momentum and short-term reversal into a composite signal.

    Parameters
    ----------
    residual_mom : pd.Series
        Residual momentum scores.
    reversal : pd.Series
        1-month reversal scores.
    sector_map : Mapping[str, str] | None, optional
        Ticker to sector mapping.
    weight_mom : float, default 0.5
        Weight assigned to residual momentum.
    weight_rev : float, default 0.5
        Weight assigned to reversal.
    zscore_inputs : bool, default True
        Whether to z-score residual_mom and reversal within sector before blending.
    zscore_output : bool, default True
        Whether to z-score the blended composite signal within sector.

    Returns
    -------
    pd.Series
        Composite signal per stock.
    """
    tickers = residual_mom.index.union(reversal.index)
    r_mom = residual_mom.reindex(tickers)
    r_rev = reversal.reindex(tickers)

    if zscore_inputs:
        r_mom_z = within_sector_zscore(r_mom, sector_map)
        r_rev_z = within_sector_zscore(r_rev, sector_map)
    else:
        r_mom_z = r_mom
        r_rev_z = r_rev

    # Where both exist, blend them; where only one exists, fallback to available signal
    composite = pd.Series(np.nan, index=tickers, dtype=float)
    both_valid = r_mom_z.notna() & r_rev_z.notna()
    composite[both_valid] = weight_mom * r_mom_z[both_valid] + weight_rev * r_rev_z[both_valid]

    only_mom = r_mom_z.notna() & r_rev_z.isna()
    if weight_mom > 0:
        composite[only_mom] = r_mom_z[only_mom]

    only_rev = r_rev_z.notna() & r_mom_z.isna()
    if weight_rev > 0:
        composite[only_rev] = r_rev_z[only_rev]

    if zscore_output:
        composite = within_sector_zscore(composite, sector_map)

    return composite


def build_cs_neutral_weights(
    composite_signal: pd.Series,
    sector_map: Mapping[str, str] | None = None,
    *,
    k: int | None = 25,
    k_per_sector: int | None = None,
    target_long: float = 0.9,
    target_short: float = 0.9,
) -> dict[str, float]:
    """Construct a dollar-neutral cross-sectional portfolio from a composite signal.

    Guarantees:
    1. Dollar neutrality: sum of long weights = +target_long, sum of short weights = -target_short,
       net dollar exposure = target_long - target_short = 0.0 (when target_long == target_short == 0.9).
    2. Reg T headroom: total gross leverage = target_long + target_short = 1.8 <= 2.0.
    3. Equal counts per sector side: for every sector in the portfolio,
       count(longs in sector) == count(shorts in sector).

    Parameters
    ----------
    composite_signal : pd.Series
        Signal per stock (higher = long candidate, lower = short candidate).
    sector_map : Mapping[str, str] | None, optional
        Ticker to sector mapping.
    k : int | None, default 25
        Total number of long positions and short positions (k longs, k shorts).
        Used when k_per_sector is None.
    k_per_sector : int | None, optional
        Number of long and short positions per sector.
        If specified, overrides k and selects exactly k_per_sector longs and
        k_per_sector shorts in each eligible sector.
    target_long : float, default 0.9
        Total weight of the long book (0.9 for Reg T headroom).
    target_short : float, default 0.9
        Total absolute weight of the short book (0.9 for Reg T headroom).

    Returns
    -------
    dict[str, float]
        Dictionary mapping ticker to signed portfolio weight.
    """
    valid_sig = composite_signal.dropna()
    if valid_sig.empty:
        return {}

    if sector_map is None:
        try:
            from tradingbot.utils.universes import SP500_SECTOR

            sector_map = SP500_SECTOR
        except ImportError:
            sector_map = {}

    # Group valid signals by sector
    sector_stocks: dict[str, list[tuple[str, float]]] = {}
    for ticker, val in valid_sig.items():
        sec = sector_map.get(ticker, "UNKNOWN")
        sector_stocks.setdefault(sec, []).append((ticker, float(val)))

    # Sort each sector's stocks descending by score
    for sec in sector_stocks:
        sector_stocks[sec].sort(key=lambda x: x[1], reverse=True)

    long_picks: list[str] = []
    short_picks: list[str] = []

    if k_per_sector is not None:
        if k_per_sector <= 0:
            return {}
        # Select exactly k_per_sector longs and k_per_sector shorts per sector
        for _sec, items in sector_stocks.items():
            n = len(items)
            # Need at least 2 * k_per_sector distinct names in this sector
            if n < 2 * k_per_sector:
                continue
            for i in range(k_per_sector):
                long_picks.append(items[i][0])
                short_picks.append(items[n - 1 - i][0])
    else:
        if k is None or k <= 0:
            return {}
        # We need total k pairs (long, short) chosen across sectors such that
        # every selected sector has equal counts of longs and shorts.
        # Form candidate pairs (long, short) per sector with score spread = score_long - score_short.
        pair_candidates: list[tuple[float, str, str, str]] = []
        for sec, items in sector_stocks.items():
            n = len(items)
            max_pairs = n // 2
            for i in range(max_pairs):
                top_sym, top_score = items[i]
                bot_sym, bot_score = items[n - 1 - i]
                spread = top_score - bot_score
                # candidate: (spread, sector, top_sym, bot_sym)
                pair_candidates.append((spread, sec, top_sym, bot_sym))

        if not pair_candidates:
            return {}

        # Sort candidate pairs by spread descending (strongest alpha pairs first)
        pair_candidates.sort(key=lambda x: x[0], reverse=True)

        selected_pairs = pair_candidates[:k]
        for _, _, top_sym, bot_sym in selected_pairs:
            long_picks.append(top_sym)
            short_picks.append(bot_sym)

    n_long = len(long_picks)
    n_short = len(short_picks)

    if n_long == 0 or n_short == 0:
        return {}

    w_long = target_long / n_long
    w_short = -target_short / n_short

    weights: dict[str, float] = {}
    for sym in long_picks:
        weights[sym] = round(w_long, 8)
    for sym in short_picks:
        weights[sym] = round(w_short, 8)

    return weights


def scale_beta_to_benchmark(
    weights: dict[str, float],
    prices: pd.DataFrame,
    benchmark_prices: pd.Series,
    *,
    lookback_bars: int = 252,
    min_history: int = 60,
    target_beta: float = 0.0,
    benchmark_symbol: str = "QQQ",
    mode: Literal["overlay", "reweight"] = "overlay",
) -> dict[str, float]:
    """Scale or hedge the portfolio's beta to a benchmark (e.g. QQQ).

    Parameters
    ----------
    weights : dict[str, float]
        Signed portfolio weights (longs positive, shorts negative).
    prices : pd.DataFrame
        Historical price series of stocks in the portfolio.
    benchmark_prices : pd.Series
        Historical price series of the benchmark (e.g. QQQ).
    lookback_bars : int, default 252
        Lookback window in bars to calculate beta.
    min_history : int, default 60
        Minimum aligned bars required to calculate beta.
    target_beta : float, default 0.0
        Target portfolio beta to benchmark.
    benchmark_symbol : str, default 'QQQ'
        Symbol of the benchmark to use when adding an overlay position.
    mode : {'overlay', 'reweight'}, default 'overlay'
        - 'overlay': adds an explicit hedge position in benchmark_symbol equal to (target_beta - beta_port).
        - 'reweight': scales the weights of individual long and short positions so that net beta is target_beta,
          while preserving total long weight (+0.9) and total short weight (-0.9).

    Returns
    -------
    dict[str, float]
        Adjusted portfolio weights.
    """
    if not weights or prices.empty or benchmark_prices.empty:
        return dict(weights)

    recent_prices = prices.iloc[-lookback_bars - 1 :]
    recent_bench = benchmark_prices.reindex(recent_prices.index)

    stock_rets = recent_prices.pct_change(fill_method=None).iloc[1:]
    bench_rets = recent_bench.pct_change(fill_method=None).iloc[1:]

    # Calculate portfolio return series
    stock_cols = [c for c in weights if c in stock_rets.columns and c != benchmark_symbol]
    if not stock_cols:
        return dict(weights)

    aligned_stock_rets = stock_rets[stock_cols]
    weight_vec = pd.Series({c: weights[c] for c in stock_cols})
    port_rets = aligned_stock_rets.dot(weight_vec)

    aligned = pd.concat([port_rets.rename("port"), bench_rets.rename("bench")], axis=1).dropna()
    if len(aligned) < min_history:
        return dict(weights)

    var_b = float(np.var(aligned["bench"], ddof=1))
    if var_b <= 1e-12:
        return dict(weights)

    cov_pb = float(np.cov(aligned["port"], aligned["bench"], ddof=1)[0, 1])
    port_beta = cov_pb / var_b

    out = dict(weights)

    if mode == "overlay":
        hedge_weight = target_beta - port_beta
        if abs(hedge_weight) > 1e-6:
            out[benchmark_symbol] = round(out.get(benchmark_symbol, 0.0) + hedge_weight, 8)
        return out

    elif mode == "reweight":
        # Calculate individual stock betas to benchmark
        longs = {s: w for s, w in weights.items() if w > 0 and s in stock_cols}
        shorts = {s: w for s, w in weights.items() if w < 0 and s in stock_cols}

        if not longs or not shorts:
            return out

        betas: dict[str, float] = {}
        for s in stock_cols:
            df_s = pd.concat([stock_rets[s], bench_rets], axis=1).dropna()
            if len(df_s) >= min_history:
                vb = float(np.var(df_s.iloc[:, 1], ddof=1))
                if vb > 1e-12:
                    betas[s] = float(np.cov(df_s.iloc[:, 0], df_s.iloc[:, 1], ddof=1)[0, 1]) / vb
                else:
                    betas[s] = 1.0
            else:
                betas[s] = 1.0

        beta_long = sum(longs[s] * betas[s] for s in longs)
        beta_short = sum(abs(shorts[s]) * betas[s] for s in shorts)

        target_total_long = sum(longs.values())
        target_total_short = sum(shorts.values())

        if beta_long > 1e-6 and beta_short > 1e-6:
            # Scale long side and short side so their beta contributions balance
            avg_beta = (beta_long + beta_short) / 2.0
            # Normalize long weights
            raw_w_long = {s: longs[s] * (avg_beta / betas[s]) for s in longs}
            sum_rw_long = sum(raw_w_long.values())
            for s in longs:
                out[s] = round(raw_w_long[s] * (target_total_long / sum_rw_long), 8)

            # Normalize short weights
            raw_w_short = {s: shorts[s] * (avg_beta / betas[s]) for s in shorts}
            sum_rw_short = sum(raw_w_short.values())
            for s in shorts:
                out[s] = round(raw_w_short[s] * (target_total_short / sum_rw_short), 8)

        return out

    return out


def build_cs_neutral_book(
    prices: pd.DataFrame,
    sector_map: Mapping[str, str] | None = None,
    *,
    market_prices: pd.Series | str | None = None,
    benchmark_prices: pd.Series | str | None = None,
    weight_mom: float = 0.5,
    weight_rev: float = 0.5,
    k: int | None = 25,
    k_per_sector: int | None = None,
    target_long: float = 0.9,
    target_short: float = 0.9,
    beta_scale: bool = False,
    benchmark_symbol: str = "QQQ",
    regression_lookback: int = 756,
    momentum_bars: int = 252,
    skip_bars: int = 21,
    reversal_lookback: int = 21,
    min_history: int = 252,
    standardize_mom: bool = False,
) -> dict[str, float]:
    """High-level pure pipeline function to generate a cross-sectional market-neutral stock book.

    Ties together:
    1. True residual momentum (12-1 residuals from 36m regression on market).
    2. 1-month short-term reversal.
    3. Within-sector z-scoring.
    4. Composite signal generation.
    5. Top-k long / bottom-k short selection with equal counts per sector side,
       long = short = 0.9 (Reg T headroom).
    6. Optional beta scaling to benchmark (e.g. QQQ).

    Parameters
    ----------
    prices : pd.DataFrame
        Historical price series (dates x tickers).
    sector_map : Mapping[str, str] | None, optional
        Ticker to sector mapping (defaults to SP500_SECTOR if None).
    market_prices : pd.Series | str | None, optional
        Market benchmark for residual regression (e.g. SPY).
    benchmark_prices : pd.Series | str | None, optional
        Benchmark for optional beta scaling (e.g. QQQ).
    weight_mom : float, default 0.5
        Weight for residual momentum in composite.
    weight_rev : float, default 0.5
        Weight for 1-month reversal in composite.
    k : int | None, default 25
        Total positions per side.
    k_per_sector : int | None, optional
        Positions per sector side.
    target_long : float, default 0.9
        Target gross long weight.
    target_short : float, default 0.9
        Target gross short weight.
    beta_scale : bool, default False
        Whether to apply beta scaling/hedging to benchmark.
    benchmark_symbol : str, default 'QQQ'
        Symbol of the benchmark for beta scaling overlay.

    Returns
    -------
    dict[str, float]
        Target signed weights for the neutral stock book.
    """
    if prices.empty:
        return {}

    # Separate stock prices from market and benchmark series
    drop_cols: set[str] = set()
    mkt_s: pd.Series | None = None
    if isinstance(market_prices, str) and market_prices in prices.columns:
        drop_cols.add(market_prices)
        mkt_s = prices[market_prices]
    elif isinstance(market_prices, pd.Series):
        mkt_s = market_prices
    elif "SPY" in prices.columns and ("SPY" not in (sector_map or {})):
        drop_cols.add("SPY")
        mkt_s = prices["SPY"]

    bench_s: pd.Series | None = None
    if isinstance(benchmark_prices, str) and benchmark_prices in prices.columns:
        drop_cols.add(benchmark_prices)
        bench_s = prices[benchmark_prices]
    elif isinstance(benchmark_prices, pd.Series):
        bench_s = benchmark_prices
    elif benchmark_symbol in prices.columns and (benchmark_symbol not in (sector_map or {})):
        drop_cols.add(benchmark_symbol)
        bench_s = prices[benchmark_symbol]

    stock_prices = prices.drop(columns=list(drop_cols)) if drop_cols else prices

    # 1. Compute residual momentum
    res_mom = compute_residual_momentum(
        prices=stock_prices,
        market_prices=mkt_s,
        regression_lookback=regression_lookback,
        momentum_bars=momentum_bars,
        skip_bars=skip_bars,
        min_history=min_history,
        standardize=standardize_mom,
    )

    # 2. Compute 1-month reversal
    rev = compute_short_term_reversal(
        prices=stock_prices,
        lookback_bars=reversal_lookback,
    )

    # 3 & 4. Within-sector z-scoring and composite signal
    comp_sig = compute_composite_signal(
        residual_mom=res_mom,
        reversal=rev,
        sector_map=sector_map,
        weight_mom=weight_mom,
        weight_rev=weight_rev,
        zscore_inputs=True,
        zscore_output=True,
    )

    # 5. Top-k long / bottom-k short portfolio weights
    weights = build_cs_neutral_weights(
        composite_signal=comp_sig,
        sector_map=sector_map,
        k=k,
        k_per_sector=k_per_sector,
        target_long=target_long,
        target_short=target_short,
    )

    # 6. Optional beta scaling to QQQ
    if beta_scale and weights and bench_s is not None:
        weights = scale_beta_to_benchmark(
            weights=weights,
            prices=stock_prices,
            benchmark_prices=bench_s,
            benchmark_symbol=benchmark_symbol,
        )

    return weights
