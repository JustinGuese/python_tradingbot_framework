"""TradingView Slingshot and Chandelier Exit (Everget) indicators.

Pure pandas, causal (no look-ahead), no DB, no network, and no Bot imports.
Warmup values are NaN / False, never filled with 0.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tradingbot.utils.tv_core import atr, ema, to_ohlcv


def _clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize price frame using to_ohlcv from tv_core safely."""
    if df is None or df.empty:
        return to_ohlcv(df)

    vol_cols = [c for c in df.columns if str(c).lower() == "volume"]
    if vol_cols:
        vol_col = vol_cols[0]
        df_no_vol = df.drop(columns=vol_cols)
        out = to_ohlcv(df_no_vol)
        try:
            ts_cols = [c for c in df.columns if str(c).lower() == "timestamp"]
            if ts_cols:
                s = pd.Series(
                    df[vol_col].to_numpy(),
                    index=pd.to_datetime(df[ts_cols[0]]),
                )
            else:
                s = pd.Series(df[vol_col].to_numpy(), index=df.index)
            if s.index.tz is not None:
                s.index = s.index.tz_convert(None)
            s = s[~s.index.duplicated(keep="last")]
            out["volume"] = pd.to_numeric(s.reindex(out.index), errors="coerce").fillna(0.0).astype(float)
        except Exception:
            out["volume"] = 0.0
        return out

    return to_ohlcv(df)


def slingshot(
    df: pd.DataFrame,
    fast: int = 34,
    slow: int = 50,
    max_extension_atr: float = 2.0,
    atr_period: int = 14,
) -> pd.DataFrame:
    """Slingshot EMA channel pullback and extension indicator.

    Parameters:
        df: DataFrame containing OHLCV price series.
        fast: Fast EMA period (default 34).
        slow: Slow EMA period (default 50).
        max_extension_atr: Multiplier for ATR extension threshold (default 2.0).
        atr_period: ATR period for extension measurement (default 14).

    Returns:
        DataFrame with columns:
            ema_fast: Fast EMA series.
            ema_slow: Slow EMA series.
            bull_trend: True where fast EMA > slow EMA.
            bear_trend: True where fast EMA < slow EMA.
            in_zone: True where bar low/high touches the fast/slow channel.
            pullback_long: True in bull trend when bar is in zone, closes >= slow EMA,
                and is not overextended.
            pullback_short: True in bear trend when bar is in zone, closes <= slow EMA,
                and is not overextended.
            overextended: True when close is > max_extension_atr ATR above channel top (long)
                or < max_extension_atr ATR below channel bottom (short).
    """
    if fast <= 0:
        raise ValueError(f"fast period must be positive, got {fast}")
    if slow <= 0:
        raise ValueError(f"slow period must be positive, got {slow}")
    if atr_period <= 0:
        raise ValueError(f"atr_period must be positive, got {atr_period}")
    if max_extension_atr < 0:
        raise ValueError(f"max_extension_atr must be non-negative, got {max_extension_atr}")

    df_norm = _clean_ohlcv(df)
    cols = [
        "ema_fast",
        "ema_slow",
        "bull_trend",
        "bear_trend",
        "in_zone",
        "pullback_long",
        "pullback_short",
        "overextended",
    ]

    if df_norm.empty:
        return pd.DataFrame(
            columns=cols,
            index=df_norm.index,
            dtype=float,
        ).astype(
            {
                "bull_trend": bool,
                "bear_trend": bool,
                "in_zone": bool,
                "pullback_long": bool,
                "pullback_short": bool,
                "overextended": bool,
            }
        )

    ema_fast = ema(df_norm["close"], fast)
    ema_slow = ema(df_norm["close"], slow)
    atr_series = atr(df_norm, atr_period)

    valid_mask = ema_fast.notna() & ema_slow.notna() & atr_series.notna()

    bull_trend_raw = ema_fast > ema_slow
    bear_trend_raw = ema_fast < ema_slow

    ch_top = np.maximum(ema_fast, ema_slow)
    ch_bot = np.minimum(ema_fast, ema_slow)

    # Bar touches the fast/slow channel if interval [low, high] overlaps [ch_bot, ch_top]
    in_zone_raw = (df_norm["low"] <= ch_top) & (df_norm["high"] >= ch_bot)

    # Overextension
    overextended_long = bull_trend_raw & (df_norm["close"] > ch_top + max_extension_atr * atr_series)
    overextended_short = bear_trend_raw & (df_norm["close"] < ch_bot - max_extension_atr * atr_series)
    overextended_raw = overextended_long | overextended_short

    # Pullbacks: held within/above slow EMA for longs, held within/below slow EMA for shorts
    pullback_long_raw = bull_trend_raw & in_zone_raw & (df_norm["close"] >= ema_slow) & (~overextended_raw)
    pullback_short_raw = bear_trend_raw & in_zone_raw & (df_norm["close"] <= ema_slow) & (~overextended_raw)

    return pd.DataFrame(
        {
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "bull_trend": (bull_trend_raw & valid_mask).fillna(False).astype(bool),
            "bear_trend": (bear_trend_raw & valid_mask).fillna(False).astype(bool),
            "in_zone": (in_zone_raw & valid_mask).fillna(False).astype(bool),
            "pullback_long": (pullback_long_raw & valid_mask).fillna(False).astype(bool),
            "pullback_short": (pullback_short_raw & valid_mask).fillna(False).astype(bool),
            "overextended": (overextended_raw & valid_mask).fillna(False).astype(bool),
        },
        index=df_norm.index,
    )


def chandelier_exit(
    df: pd.DataFrame,
    period: int = 22,
    mult: float = 3.0,
    use_close: bool = False,
) -> pd.DataFrame:
    """Chandelier Exit indicator following Alex Orekhov (Everget) TradingView script.

    Parameters:
        df: DataFrame containing OHLCV price series.
        period: Lookback period for ATR and highest/lowest extremes (default 22).
        mult: ATR multiplier for trailing stop distance (default 3.0).
        use_close: If True, extremums use close instead of high/low (default False).

    Returns:
        DataFrame with columns:
            long_stop: Long trailing stop level, ratcheted up (never lowered while
                previous close stayed above it).
            short_stop: Short trailing stop level, ratcheted down (never raised while
                previous close stayed below it).
            direction: 1.0 for long, -1.0 for short, NaN during warmup.
            buy: Strict crossover to long (True only on the transition bar).
            sell: Strict crossunder to short (True only on the transition bar).
    """
    if period <= 0:
        raise ValueError(f"period must be positive, got {period}")
    if mult <= 0:
        raise ValueError(f"mult must be positive, got {mult}")

    df_norm = _clean_ohlcv(df)
    cols = ["long_stop", "short_stop", "direction", "buy", "sell"]

    if df_norm.empty:
        return pd.DataFrame(
            columns=cols,
            index=df_norm.index,
            dtype=float,
        ).astype(
            {
                "buy": bool,
                "sell": bool,
            }
        )

    n = len(df_norm)
    if n < period:
        return pd.DataFrame(
            {
                "long_stop": pd.Series(np.nan, index=df_norm.index, dtype=float),
                "short_stop": pd.Series(np.nan, index=df_norm.index, dtype=float),
                "direction": pd.Series(np.nan, index=df_norm.index, dtype=float),
                "buy": pd.Series(False, index=df_norm.index, dtype=bool),
                "sell": pd.Series(False, index=df_norm.index, dtype=bool),
            },
            index=df_norm.index,
        )

    high_source = df_norm["close"] if use_close else df_norm["high"]
    low_source = df_norm["close"] if use_close else df_norm["low"]

    atr_series = atr(df_norm, period)
    highest_high = high_source.rolling(window=period, min_periods=period).max()
    lowest_low = low_source.rolling(window=period, min_periods=period).min()

    raw_long = highest_high - mult * atr_series
    raw_short = lowest_low + mult * atr_series

    close_arr = df_norm["close"].to_numpy(dtype=float)
    raw_long_arr = raw_long.to_numpy(dtype=float)
    raw_short_arr = raw_short.to_numpy(dtype=float)

    long_stop_arr = np.full(n, np.nan, dtype=np.float64)
    short_stop_arr = np.full(n, np.nan, dtype=np.float64)
    dir_arr = np.full(n, np.nan, dtype=np.float64)

    valid_mask = ~(np.isnan(raw_long_arr) | np.isnan(raw_short_arr) | np.isnan(close_arr))
    if not np.any(valid_mask):
        return pd.DataFrame(
            {
                "long_stop": pd.Series(np.nan, index=df_norm.index, dtype=float),
                "short_stop": pd.Series(np.nan, index=df_norm.index, dtype=float),
                "direction": pd.Series(np.nan, index=df_norm.index, dtype=float),
                "buy": pd.Series(False, index=df_norm.index, dtype=bool),
                "sell": pd.Series(False, index=df_norm.index, dtype=bool),
            },
            index=df_norm.index,
        )

    start_idx = int(np.argmax(valid_mask))

    # Initialize at first valid bar
    long_stop_arr[start_idx] = raw_long_arr[start_idx]
    short_stop_arr[start_idx] = raw_short_arr[start_idx]

    if close_arr[start_idx] > short_stop_arr[start_idx]:
        dir_arr[start_idx] = 1.0
    elif close_arr[start_idx] < long_stop_arr[start_idx]:
        dir_arr[start_idx] = -1.0
    else:
        dir_arr[start_idx] = 1.0

    # Sequential ratcheting and state tracking (Everget semantics)
    for i in range(start_idx + 1, n):
        prev_close = close_arr[i - 1]
        prev_long = long_stop_arr[i - 1]
        prev_short = short_stop_arr[i - 1]
        prev_dir = dir_arr[i - 1]

        # Ratchet long_stop: never lowered while prev_close > prev_long
        if prev_close > prev_long:
            long_stop_arr[i] = max(raw_long_arr[i], prev_long)
        else:
            long_stop_arr[i] = raw_long_arr[i]

        # Ratchet short_stop: never raised while prev_close < prev_short
        if prev_close < prev_short:
            short_stop_arr[i] = min(raw_short_arr[i], prev_short)
        else:
            short_stop_arr[i] = raw_short_arr[i]

        # Direction flip condition per Everget:
        # close > shortStopPrev ? 1 : close < longStopPrev ? -1 : dir
        curr_close = close_arr[i]
        if curr_close > prev_short:
            dir_arr[i] = 1.0
        elif curr_close < prev_long:
            dir_arr[i] = -1.0
        else:
            dir_arr[i] = prev_dir

    direction_series = pd.Series(dir_arr, index=df_norm.index, dtype=float)
    prev_dir_series = direction_series.shift(1)

    buy_series = (direction_series == 1.0) & (prev_dir_series == -1.0)
    sell_series = (direction_series == -1.0) & (prev_dir_series == 1.0)

    return pd.DataFrame(
        {
            "long_stop": pd.Series(long_stop_arr, index=df_norm.index, dtype=float),
            "short_stop": pd.Series(short_stop_arr, index=df_norm.index, dtype=float),
            "direction": direction_series,
            "buy": buy_series.fillna(False).astype(bool),
            "sell": sell_series.fillna(False).astype(bool),
        },
        index=df_norm.index,
    )
