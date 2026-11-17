"""TradingView trend-following enhancement utilities (Step S1 of PLAN-trendboost.md).

Pure pandas, causal (no lookahead), no DB, no network, and no Bot imports.
Implements:
1. inv_vol_weights: rolling inverse-volatility weighting with per-ticker and gross caps.
2. multi_speed_flags: arithmetic mean of flags across multiple speed/parameter sets.
3. soft_flags: continuous 0..1 weight based on distance between close and trend stop / ATR.
4. defensive_rotation: rotates unallocated risk budget into active defensive assets.
5. top_k_by_strength: filters active flags to top k assets ranked by strength.
6. cash_yield_returns: computes strategy returns where unallocated cash earns SHV yield (pre-2007 = 0).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from tradingbot.utils.tv_alphatrend import alphatrend
from tradingbot.utils.tv_core import atr, sma, to_ohlcv, true_range
from tradingbot.utils.tv_signals import (
    DEFAULT_PARAMS,
    SUPPORTED_KINDS,
    UNIVERSE_WIDE,
    signal_flags,
)
from tradingbot.utils.tv_slingshot_chandelier import chandelier_exit
from tradingbot.utils.tv_supertrend import supertrend
from tradingbot.utils.tv_ultimate_ma import ultimate_ma


def inv_vol_weights(
    flags_df: pd.DataFrame,
    closes: pd.DataFrame,
    target_vol: float = 0.10,
    window: int = 60,
    per_ticker_cap: float = 0.20,
    gross_cap: float = 1.0,
) -> pd.DataFrame:
    """Compute rolling inverse-volatility portfolio weights.

    Computes rolling annualized volatility (standard deviation of daily pct returns * sqrt(252))
    using the specified rolling window (default 60 days).
    Strictly causal: weights at date t use returns up to date t.
    Inverse-volatility weight = target_vol / rolling_vol.
    For tickers where flags_df > 0 (or weighted by continuous flag),
    raw_weight = flag * min(target_vol / rolling_vol, per_ticker_cap).
    Scales down proportionally if sum of raw weights > gross_cap so sum <= gross_cap,
    and ensures each weight <= per_ticker_cap.
    Handles 0, NaN, and insufficient history cleanly by assigning 0.0 weight.

    Parameters
    ----------
    flags_df : pd.DataFrame
        Matrix of signal flags (boolean or float in [0, 1]) indexed by date, columns by ticker.
    closes : pd.DataFrame
        Matrix of asset close prices indexed by date, columns by ticker.
    target_vol : float, default 0.10
        Annualized target volatility (e.g. 0.10 for 10% vol).
    window : int, default 60
        Rolling window in bars (trading days) for volatility estimation.
    per_ticker_cap : float, default 0.20
        Maximum target weight allowed for any individual ticker.
    gross_cap : float, default 1.0
        Maximum gross sum of weights across all tickers.

    Returns
    -------
    pd.DataFrame
        DataFrame of target weights matching flags_df shape and index.
    """
    if flags_df is None or flags_df.empty:
        return pd.DataFrame(index=flags_df.index if flags_df is not None else None)

    if gross_cap <= 0.0 or per_ticker_cap <= 0.0:
        return pd.DataFrame(0.0, index=flags_df.index, columns=flags_df.columns, dtype=float)

    aligned_closes = closes.reindex(index=flags_df.index, columns=flags_df.columns)
    daily_rets = aligned_closes.pct_change()

    # Rolling annualized vol (std of daily pct returns * sqrt(252))
    # Using min_periods=window to avoid premature vol estimates; warmup gives NaN
    rolling_vol = daily_rets.rolling(window=window, min_periods=window).std() * np.sqrt(252.0)

    # Inverse volatility: target_vol / rolling_vol
    with np.errstate(divide="ignore", invalid="ignore"):
        inv_vol = target_vol / rolling_vol

    # Clean non-finite values (division by zero, negative, NaN from warmup)
    inv_vol = inv_vol.replace([np.inf, -np.inf], np.nan).fillna(0.0).clip(lower=0.0)
    capped_inv_vol = inv_vol.clip(upper=per_ticker_cap)

    # Raw weight = flag * min(target_vol / rolling_vol, per_ticker_cap)
    flag_vals = flags_df.astype(float).fillna(0.0).clip(lower=0.0)
    raw_weights = flag_vals * capped_inv_vol
    raw_weights = raw_weights.clip(upper=per_ticker_cap)

    # Scale down proportionally if sum of raw weights > gross_cap
    row_sums = raw_weights.sum(axis=1)
    scale = np.where(row_sums > gross_cap, gross_cap / row_sums.replace(0.0, np.nan), 1.0)
    scaled_weights = raw_weights.mul(scale, axis=0).fillna(0.0)

    # Final sanity clip ensuring <= per_ticker_cap and >= 0.0
    final_weights = scaled_weights.clip(lower=0.0, upper=per_ticker_cap)
    return final_weights


def multi_speed_flags(
    dfs: dict[str, pd.DataFrame],
    master_idx: pd.DatetimeIndex,
    kind: str,
    param_sets: list[dict[str, Any]],
    universe: list[str] | None = None,
) -> pd.DataFrame:
    """Compute arithmetic mean of causal flags across multiple speed/parameter sets.

    Parameters
    ----------
    dfs : dict[str, pd.DataFrame]
        Dictionary mapping ticker -> OHLCV DataFrame.
    master_idx : pd.DatetimeIndex
        Common sorted DatetimeIndex for the output frame.
    kind : str
        Trend signal kind ('supertrend', 'alphatrend', 'ultimate_ma', 'slingshot_chandelier').
    param_sets : list[dict[str, Any]]
        List of parameter dictionaries defining each speed variant.
    universe : list[str] | None, default None
        List of tickers to evaluate. Defaults to UNIVERSE_WIDE if None.

    Returns
    -------
    pd.DataFrame
        DataFrame of float values in [0.0, 1.0] indexed by master_idx, columns by universe tickers.
    """
    target_uni = list(universe) if universe is not None else list(UNIVERSE_WIDE)
    if not param_sets:
        return pd.DataFrame(0.0, index=master_idx, columns=target_uni, dtype=float)

    cols: dict[str, pd.Series] = {}
    for ticker in target_uni:
        df = dfs.get(ticker)
        if df is None or df.empty:
            cols[ticker] = pd.Series(0.0, index=master_idx, dtype=float)
            continue

        flags_for_ticker: list[pd.Series] = []
        for p in param_sets:
            fl = signal_flags(df, kind=kind, params=p)
            fl_aligned = fl.reindex(master_idx).fillna(False).astype(float)
            flags_for_ticker.append(fl_aligned)

        # Arithmetic mean across speeds
        cols[ticker] = sum(flags_for_ticker) / float(len(param_sets))

    return pd.DataFrame(cols, index=master_idx).fillna(0.0)


def soft_flags(
    dfs: dict[str, pd.DataFrame],
    master_idx: pd.DatetimeIndex,
    kind: str,
    params: dict[str, Any] | None = None,
    universe: list[str] | None = None,
    max_atr_dist: float = 3.0,
) -> pd.DataFrame:
    """Compute continuous trend weight in [0.0, 1.0] based on ATR-normalized distance to trend stop.

    When the trend is active, weight is proportional to distance from close to the trend stop
    normalized by ATR: clip((close - stop) / (max_atr_dist * ATR), 0.0, 1.0).
    When the trend is inactive (or close <= stop), weight is 0.0.

    Parameters
    ----------
    dfs : dict[str, pd.DataFrame]
        Dictionary mapping ticker -> OHLCV DataFrame.
    master_idx : pd.DatetimeIndex
        Common sorted DatetimeIndex for output.
    kind : str
        One of {'supertrend', 'alphatrend', 'ultimate_ma', 'slingshot_chandelier'}.
    params : dict[str, Any] | None, default None
        Indicator hyperparameters. Uses DEFAULT_PARAMS if omitted.
    universe : list[str] | None, default None
        List of tickers to evaluate. Defaults to UNIVERSE_WIDE if None.
    max_atr_dist : float, default 3.0
        Distance in ATRs at which the soft flag reaches its maximum weight (1.0).

    Returns
    -------
    pd.DataFrame
        DataFrame of continuous weights in [0.0, 1.0] indexed by master_idx.
    """
    target_uni = list(universe) if universe is not None else list(UNIVERSE_WIDE)
    kind_clean = str(kind).lower().strip().replace("-", "_")
    p = dict(DEFAULT_PARAMS.get(kind_clean, {}))
    if params:
        p.update(params)

    eff_max_dist = max(max_atr_dist, 1e-4)
    cols: dict[str, pd.Series] = {}

    for ticker in target_uni:
        df = dfs.get(ticker)
        if df is None or df.empty:
            cols[ticker] = pd.Series(0.0, index=master_idx, dtype=float)
            continue

        clean_df = to_ohlcv(df)
        if clean_df.empty:
            cols[ticker] = pd.Series(0.0, index=master_idx, dtype=float)
            continue

        close = clean_df["close"]

        if kind_clean == "supertrend":
            period = int(p.get("period", 10))
            mult = float(p.get("mult", 3.0))
            st = supertrend(clean_df, period=period, mult=mult)
            direction = st["direction"]
            stop = st["supertrend"]
            atr_val = atr(clean_df, period)
            is_active = direction == 1.0
            htf = p.get("htf")
            if htf:
                fl = signal_flags(clean_df, kind="supertrend", params=p)
                is_active = fl

        elif kind_clean == "alphatrend":
            period = int(p.get("period", 14))
            mult = float(p.get("mult", 1.0))
            use_mfi = p.get("use_mfi")
            at = alphatrend(clean_df, period=period, mult=mult, use_mfi=use_mfi)
            stop = at["alphatrend"]
            tr = true_range(clean_df)
            atr_val = sma(tr, period)
            is_active = signal_flags(clean_df, kind="alphatrend", params=p)

        elif kind_clean == "slingshot_chandelier":
            ch_period = int(p.get("ch_period", p.get("period", 22)))
            ch_mult = float(p.get("ch_mult", p.get("mult", 3.0)))
            atr_period = int(p.get("atr_period", 14))
            ce = chandelier_exit(clean_df, period=ch_period, mult=ch_mult)
            stop = ce["long_stop"]
            atr_val = atr(clean_df, atr_period)
            is_active = signal_flags(clean_df, kind="slingshot_chandelier", params=p)

        elif kind_clean == "ultimate_ma":
            ma_kind = str(p.get("ma_type", p.get("ma_kind", p.get("kind", "hma")))).lower().strip()
            length = int(p.get("length", 20))
            slope_bars = int(p.get("slope_bars", 1))
            uma = ultimate_ma(clean_df, kind=ma_kind, length=length, slope_bars=slope_bars)
            stop = uma["ma"]
            atr_val = atr(clean_df, 14)
            is_active = uma["direction"] == 1.0
        else:
            raise ValueError(f"Unknown signal kind '{kind}'. Expected one of: {', '.join(SUPPORTED_KINDS)}")

        dist = close - stop
        denom = atr_val * eff_max_dist
        with np.errstate(divide="ignore", invalid="ignore"):
            raw_ratio = dist / denom.replace(0.0, np.nan)
        soft_val = raw_ratio.clip(lower=0.0, upper=1.0)
        soft_series = soft_val.where(is_active, 0.0).fillna(0.0)
        cols[ticker] = soft_series.reindex(master_idx).fillna(0.0)

    return pd.DataFrame(cols, index=master_idx).fillna(0.0)


def defensive_rotation(
    flags_df: pd.DataFrame,
    closes: pd.DataFrame | None = None,
    defensive: tuple[str, ...] = ("TLT", "IEF", "GLD", "SHV"),
    gross_cap: float = 1.0,
    per_ticker_cap: float = 0.20,
    weights_df: pd.DataFrame | None = None,
    defensive_flags: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Rotate unallocated risk budget into active defensive assets.

    When risk assets are off (unallocated weight = gross_cap - sum(risk_weights) > 0),
    allocates weight to defensives whose own trend/flag is on.
    Total weight across risk + defensive assets never exceeds gross_cap,
    and each individual weight never exceeds per_ticker_cap.

    Parameters
    ----------
    flags_df : pd.DataFrame
        Matrix of flags or weights for universe assets.
    closes : pd.DataFrame | None, default None
        Optional close price frame.
    defensive : tuple[str, ...], default ("TLT", "IEF", "GLD", "SHV")
        Tuple of defensive ticker symbols.
    gross_cap : float, default 1.0
        Maximum total gross portfolio weight.
    per_ticker_cap : float, default 0.20
        Maximum weight allocated to any single asset.
    weights_df : pd.DataFrame | None, default None
        Pre-computed risk asset weights. If None, derives risk weights from flags_df.
    defensive_flags : pd.DataFrame | None, default None
        Explicit flags matrix for defensive assets. If None, checks flags_df.

    Returns
    -------
    pd.DataFrame
        DataFrame of portfolio weights across risk and defensive assets,
        strictly obeying per-ticker and gross caps.
    """
    if flags_df is None or flags_df.empty:
        return pd.DataFrame()

    def_list = list(defensive)
    risk_cols = [c for c in flags_df.columns if c not in def_list]

    # Derive base risk weights
    if weights_df is not None:
        avail_risk = [c for c in risk_cols if c in weights_df.columns]
        base_risk_w = weights_df[avail_risk].copy()
    else:
        # Check if flags_df is already weights (values sum <= gross_cap and <= per_ticker_cap)
        # or boolean / raw flags (> 1 or sum > gross_cap)
        is_bool = any(pd.api.types.is_bool_dtype(dt) for dt in flags_df.dtypes)
        max_sum = flags_df[risk_cols].sum(axis=1).max() if risk_cols else 0.0
        if is_bool or max_sum > gross_cap + 1e-4:
            # Convert raw flags to weights
            risk_f = flags_df[risk_cols].astype(float)
            active_cnt = (risk_f > 0).sum(axis=1)
            raw_w = risk_f.div(active_cnt.replace(0, np.nan), axis=0).clip(upper=per_ticker_cap).fillna(0.0)
            r_sums = raw_w.sum(axis=1)
            sc = np.where(r_sums > gross_cap, gross_cap / r_sums.replace(0.0, np.nan), 1.0)
            base_risk_w = raw_w.mul(sc, axis=0).fillna(0.0)
        else:
            base_risk_w = flags_df[risk_cols].copy()

    # Calculate unallocated risk budget at each date
    risk_alloc_sum = base_risk_w.sum(axis=1)
    unallocated = (gross_cap - risk_alloc_sum).clip(lower=0.0)

    # Prepare defensive weights frame
    def_weights = pd.DataFrame(0.0, index=flags_df.index, columns=def_list, dtype=float)

    # Allocate unallocated budget among active defensives at each date
    for dt in flags_df.index:
        u = float(unallocated.loc[dt])
        if u <= 1e-7:
            continue

        active_defs: list[str] = []
        for d in def_list:
            if defensive_flags is not None and d in defensive_flags.columns:
                if bool(defensive_flags.loc[dt, d] > 0):
                    active_defs.append(d)
            elif d in flags_df.columns and bool(flags_df.loc[dt, d] > 0):
                active_defs.append(d)

        if active_defs:
            w_each = min(per_ticker_cap, u / float(len(active_defs)))
            for d in active_defs:
                def_weights.loc[dt, d] = w_each

    # Combine risk weights and defensive weights
    out_cols: list[str] = []
    frames: list[pd.DataFrame] = []

    if not base_risk_w.empty:
        out_cols.extend(base_risk_w.columns)
        frames.append(base_risk_w)

    def_to_add = [d for d in def_list if d not in out_cols]
    if def_to_add:
        frames.append(def_weights[def_to_add])

    out = pd.concat(frames, axis=1) if frames else pd.DataFrame(index=flags_df.index)

    # Strictly enforce gross_cap and per_ticker_cap
    row_sums = out.sum(axis=1)
    scale = np.where(row_sums > gross_cap, gross_cap / row_sums.replace(0.0, np.nan), 1.0)
    out = out.mul(scale, axis=0).clip(lower=0.0, upper=per_ticker_cap)
    return out


def top_k_by_strength(
    flags_df: pd.DataFrame,
    strength_df: pd.DataFrame,
    k: int = 5,
) -> pd.DataFrame:
    """Filter active flags to top k tickers ranked by cross-sectional strength at date t.

    Parameters
    ----------
    flags_df : pd.DataFrame
        Matrix of signal flags (boolean or float weights) indexed by date x ticker.
    strength_df : pd.DataFrame
        Matrix of strength scores (e.g. 12-month return, momentum score, Sharpe).
    k : int, default 5
        Maximum number of active tickers to retain per date.

    Returns
    -------
    pd.DataFrame
        Filtered flags DataFrame with at most k active tickers per date.
        Retains original flag values/types for selected tickers, sets others to 0 / False.
    """
    if flags_df is None or flags_df.empty:
        return flags_df.copy() if flags_df is not None else pd.DataFrame()

    is_bool = pd.api.types.is_bool_dtype(flags_df.dtypes.iloc[0]) if len(flags_df.columns) > 0 else False

    if k <= 0:
        if is_bool:
            return pd.DataFrame(False, index=flags_df.index, columns=flags_df.columns, dtype=bool)
        return pd.DataFrame(0.0, index=flags_df.index, columns=flags_df.columns, dtype=float)

    aligned_str = strength_df.reindex(index=flags_df.index, columns=flags_df.columns)
    clean_str = aligned_str.fillna(-1e12)

    flag_active = (flags_df > 0) if not is_bool else flags_df
    # Inactive tickers masked to -infinity so they rank last
    masked_str = clean_str.where(flag_active, -np.inf)

    # Rank descending across columns (method='first' breaks ties deterministically)
    ranks = masked_str.rank(axis=1, ascending=False, method="first")
    keep_mask = (ranks <= k) & flag_active

    if is_bool:
        return keep_mask.astype(bool)
    return flags_df.where(keep_mask, 0.0)


def cash_yield_returns(
    weights_df: pd.DataFrame,
    closes: pd.DataFrame,
    shv_close: pd.Series,
    cost_bp: float = 0.0005,
) -> pd.Series:
    """Compute strategy returns where unallocated weight (1 - sum(weights)) earns SHV return.

    Pre-2007 dates earn 0.0 cash yield (SHV launched in 2007).
    Strictly causal: positions held at date t are weights decided at date t-1.

    Parameters
    ----------
    weights_df : pd.DataFrame
        Target weights frame indexed by date x ticker.
    closes : pd.DataFrame
        Close prices frame for assets in weights_df.
    shv_close : pd.Series
        Close prices series for SHV (iShares Short Treasury Bond ETF).
    cost_bp : float, default 0.0005
        One-way turnover cost in basis points (default 5 bps = 0.0005).

    Returns
    -------
    pd.Series
        Series of net strategy returns aligned to weights_df.index.
    """
    if weights_df is None or weights_df.empty:
        return pd.Series(dtype=float)

    aligned_closes = closes.reindex(index=weights_df.index, columns=weights_df.columns)
    asset_rets = aligned_closes.pct_change()

    # Positions held overnight from t-1 into t
    held = weights_df.shift(1).fillna(0.0)
    gross_asset = (held * asset_rets.fillna(0.0)).sum(axis=1)

    # Turnover cost applied to changes in asset positions
    turnover = held.diff().abs().sum(axis=1).fillna(0.0)

    # Cash weight held overnight from t-1 into t
    held_sum = held.sum(axis=1)
    cash_weight = (1.0 - held_sum).clip(lower=0.0)

    # SHV return for cash yield
    if shv_close is not None and not shv_close.empty:
        shv_ret = shv_close.pct_change().reindex(weights_df.index).fillna(0.0)
        # Pre-2007 earns 0.0 cash yield
        if isinstance(shv_ret.index, pd.DatetimeIndex):
            shv_ret.loc[shv_ret.index.year < 2007] = 0.0
    else:
        shv_ret = pd.Series(0.0, index=weights_df.index, dtype=float)

    cash_return = cash_weight * shv_ret
    net_return = gross_asset + cash_return - cost_bp * turnover
    return net_return
