"""
TA-only regime classification and signal logic (historic data only).

Pure functions: Hurst-style proxy from returns, z-score, regime classification,
and single-bar decision. No Bot or db dependency. Used by TARegimeAdaptiveBot.
"""

from typing import Any

import numpy as np
import pandas as pd


def hurst_proxy_from_returns(returns: pd.Series, window: int) -> float:
    """
    Variance-ratio / persistence proxy from rolling returns.

    Uses lag-1 autocorrelation of returns over the last `window` values.
    Positive autocorrelation → persistence (trend); negative → mean reversion.
    Return value in [0, 1]: 0.5 = random walk, >0.5 = trend, <0.5 = mean reversion.

    Args:
        returns: Series of returns (e.g. close.pct_change().dropna()).
        window: Number of most recent returns to use.

    Returns:
        Float in [0, 1]. Maps acf(1) from [-1,1] to [0,1] as (acf1 + 1) / 2.
        If insufficient data or zero variance, returns 0.5 (neutral).
    """
    if returns is None or len(returns) < 2 or window < 2:
        return 0.5
    tail = returns.iloc[-window:].dropna()
    if len(tail) < 2:
        return 0.5
    tail = tail.astype(float)
    if tail.var() == 0 or np.isnan(tail.var()):
        return 0.5
    acf1 = tail.autocorr(lag=1)
    if pd.isna(acf1):
        return 0.5
    # Map [-1, 1] -> [0, 1]; 0.5 = no autocorrelation
    return float(0.5 + 0.5 * np.clip(acf1, -1.0, 1.0))


def classify_ta_regime(hurst_value: float, threshold: float) -> str:
    """
    Classify regime from Hurst-style proxy.

    Args:
        hurst_value: Value from hurst_proxy_from_returns (0–1).
        threshold: Above or equal → "trend", below → "mean_reversion".

    Returns:
        "trend" | "mean_reversion"
    """
    return "trend" if hurst_value >= threshold else "mean_reversion"


def rolling_zscore(series: pd.Series, window: int, current_idx: int) -> float | None:
    """
    Z-score of series over the window ending at current_idx.

    Args:
        series: Full series (e.g. close or momentum_rsi).
        window: Lookback length.
        current_idx: Integer position of current bar (inclusive).

    Returns:
        (current_value - mean) / std over the window, or None if insufficient data.
    """
    if series is None or len(series) == 0 or window <= 0:
        return None
    start = max(0, current_idx - window + 1)
    end = current_idx + 1
    if end - start < window:
        return None
    window_slice = series.iloc[start:end]
    if window_slice.isna().all() or window_slice.std() == 0:
        return None
    current = window_slice.iloc[-1]
    if pd.isna(current):
        return None
    mean = window_slice.mean()
    std = window_slice.std()
    if std == 0 or not np.isfinite(std):
        return None
    return float((current - mean) / std)


def _safe_get(row: pd.Series, key: str, default: float = 0.0) -> float:
    """Get float from row with NaN handling. Internal use only."""
    value = row.get(key, default)
    if pd.isna(value):
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def ta_regime_decision(
    row: pd.Series,
    data: pd.DataFrame | None,
    *,
    hurst_window: int = 50,
    hurst_trend_threshold: float = 0.5,
    adx_threshold: float = 22.0,
    rsi_oversold: float = 32.0,
    rsi_overbought: float = 68.0,
    bbp_low: float = 0.1,
    bbp_high: float = 0.9,
    zscore_window: int = 0,
    zscore_entry: float = 2.0,
    macd_confirm_trend: bool = True,
    **kwargs: Any,
) -> int:
    """
    Single-bar TA regime decision: trend vs mean-reversion from historic data only.

    (1) If data is None or too short, return 0.
    (2) Compute returns from data["close"] up to row's index; Hurst proxy over
        hurst_window; classify regime.
    (3) Trend regime: ADX > adx_threshold, MACD vs signal (and optional EMA alignment) → 1/-1/0.
    (4) Mean-reversion regime: RSI oversold/overbought, BBP low/high, optional z-score → 1/-1/0.

    Args:
        row: Current bar (Series from data.iterrows()).
        data: Full DataFrame with close and TA columns (set by backtest/bot).
        hurst_window: Lookback for Hurst proxy.
        hurst_trend_threshold: Hurst >= this → trend regime.
        adx_threshold: Min ADX in trend regime.
        rsi_oversold: RSI below = oversold (mean-reversion buy).
        rsi_overbought: RSI above = overbought (mean-reversion sell).
        bbp_low: BBP below = buy zone (mean-reversion).
        bbp_high: BBP above = sell zone (mean-reversion).
        zscore_window: 0 = disable z-score filter; else rolling window for close.
        zscore_entry: |z| > this for mean-reversion entry strength.
        macd_confirm_trend: In trend regime, require MACD vs signal for direction.
        **kwargs: Ignored (for forward compatibility).

    Returns:
        -1: Sell, 0: Hold, 1: Buy.
    """
    if data is None or data.empty or "close" not in data.columns:
        return 0

    try:
        idx = data.index.get_loc(row.name)
    except (KeyError, TypeError):
        return 0

    # Need enough history for returns and Hurst window
    min_rows = hurst_window + 2
    if idx < min_rows - 1:
        return 0

    close = data["close"].iloc[: idx + 1]
    returns = close.pct_change().dropna()
    if len(returns) < hurst_window:
        return 0

    hurst_value = hurst_proxy_from_returns(returns, hurst_window)
    regime = classify_ta_regime(hurst_value, hurst_trend_threshold)

    def get(key: str, default: float = 0.0) -> float:
        return _safe_get(row, key, default)

    rsi = get("momentum_rsi", 50.0)
    macd = get("trend_macd", 0.0)
    macd_signal = get("trend_macd_signal", 0.0)
    adx = get("trend_adx", 0.0)
    bbp = get("volatility_bbp", 0.5)
    ema_fast = get("trend_ema_fast", 0.0)
    ema_slow = get("trend_ema_slow", 0.0)
    close_price = get("close", 0.0)
    if close_price <= 0:
        return 0

    if regime == "trend":
        if adx <= adx_threshold:
            return 0
        if macd > macd_signal:
            if macd_confirm_trend and (ema_fast <= 0 or ema_slow <= 0 or ema_fast <= ema_slow):
                return 0
            return 1
        if macd < macd_signal:
            if macd_confirm_trend and (ema_fast <= 0 or ema_slow <= 0 or ema_fast >= ema_slow):
                return 0
            return -1
        return 0

    # Mean-reversion regime: buy when RSI oversold and BBP low; sell when RSI overbought and BBP high
    z = None
    if zscore_window > 0:
        z = rolling_zscore(close, zscore_window, idx)
    buy_ok = rsi <= rsi_oversold and bbp <= bbp_low
    if z is not None and zscore_entry > 0:
        buy_ok = buy_ok and z <= -zscore_entry
    if buy_ok:
        return 1
    sell_ok = rsi >= rsi_overbought and bbp >= bbp_high
    if z is not None and zscore_entry > 0:
        sell_ok = sell_ok and z >= zscore_entry
    if sell_ok:
        return -1
    return 0
