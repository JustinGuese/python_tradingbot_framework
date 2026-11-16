"""TradingView Supertrend indicator and HTF direction helper.

Pure pandas, causal (lookahead_off), no DB, no network, and no Bot imports.
Warmup rows are NaN / False, never filled with 0.
Semantics follow Pine Script v5 ta.supertrend:
hl2 +/- mult * ATR, bands ratchet toward price unless previous close broke them,
direction flips on close vs previous bar's band.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tradingbot.utils.tv_core import align_htf, atr, resample_ohlcv


def supertrend(
    df: pd.DataFrame,
    period: int = 10,
    mult: float = 3.0,
) -> pd.DataFrame:
    """Calculate Supertrend indicator matching Pine Script v5 ta.supertrend semantics.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV DataFrame containing at least 'high', 'low', 'close' columns.
    period : int, default 10
        ATR period (Wilder smoothing). Must be positive.
    mult : float, default 3.0
        Band multiplier. Must be positive.

    Returns
    -------
    pd.DataFrame
        DataFrame with columns:
        - 'supertrend' (float): Active trailing stop line (lower band in uptrend, upper in downtrend).
        - 'direction' (float): +1.0 for uptrend (bullish), -1.0 for downtrend (bearish), NaN on warmup.
        - 'buy' (bool): True on the bar where direction flips from -1 to +1.
        - 'sell' (bool): True on the bar where direction flips from +1 to -1.
    """
    if period <= 0:
        raise ValueError(f"period must be positive, got {period}")
    if mult <= 0.0:
        raise ValueError(f"mult must be positive, got {mult}")

    if df is None or len(df) == 0:
        return pd.DataFrame(
            {
                "supertrend": pd.Series(dtype=float),
                "direction": pd.Series(dtype=float),
                "buy": pd.Series(dtype=bool),
                "sell": pd.Series(dtype=bool),
            },
            index=df.index if df is not None else pd.DatetimeIndex([], dtype="datetime64[ns]"),
        )

    cols_map = {str(c).lower(): c for c in df.columns}
    for req in ("high", "low", "close"):
        if req not in cols_map:
            raise ValueError(f"DataFrame missing required column: '{req}'")

    high = pd.to_numeric(df[cols_map["high"]], errors="coerce").to_numpy(dtype=float)
    low = pd.to_numeric(df[cols_map["low"]], errors="coerce").to_numpy(dtype=float)
    close = pd.to_numeric(df[cols_map["close"]], errors="coerce").to_numpy(dtype=float)

    hl2 = (high + low) / 2.0

    # ATR from tv_core uses Wilder RMA smoothing
    clean_df = pd.DataFrame(
        {"high": high, "low": low, "close": close},
        index=df.index,
    )
    atr_vals = atr(clean_df, period).to_numpy(dtype=float)

    basic_upper = hl2 + mult * atr_vals
    basic_lower = hl2 - mult * atr_vals

    n = len(df)
    final_upper = np.zeros(n, dtype=float)
    final_lower = np.zeros(n, dtype=float)
    supertrend_vals = np.full(n, np.nan, dtype=float)
    direction = np.full(n, np.nan, dtype=float)

    for i in range(n):
        if np.isnan(atr_vals[i]) or np.isnan(close[i]):
            final_upper[i] = np.nan
            final_lower[i] = np.nan
            continue

        if i == 0 or np.isnan(direction[i - 1]):
            final_upper[i] = basic_upper[i]
            final_lower[i] = basic_lower[i]
            # Direction initialized based on close vs hl2: +1 if close >= hl2 else -1
            direction[i] = 1.0 if close[i] >= hl2[i] else -1.0
            supertrend_vals[i] = final_lower[i] if direction[i] == 1.0 else final_upper[i]
            continue

        # Ratchet logic matching Pine Script v5 ta.supertrend:
        # Lower band only moves up unless previous close broke below it
        if basic_lower[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1]:
            final_lower[i] = basic_lower[i]
        else:
            final_lower[i] = final_lower[i - 1]

        # Upper band only moves down unless previous close broke above it
        if basic_upper[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1]:
            final_upper[i] = basic_upper[i]
        else:
            final_upper[i] = final_upper[i - 1]

        # Direction flip logic: close vs previous bar's band
        if direction[i - 1] == -1.0 and close[i] > final_upper[i - 1]:
            direction[i] = 1.0
        elif direction[i - 1] == 1.0 and close[i] < final_lower[i - 1]:
            direction[i] = -1.0
        else:
            direction[i] = direction[i - 1]

        supertrend_vals[i] = final_lower[i] if direction[i] == 1.0 else final_upper[i]

    dir_series = pd.Series(direction, index=df.index, name="direction")
    prev_dir = dir_series.shift(1)
    buy = (dir_series == 1.0) & (prev_dir == -1.0)
    sell = (dir_series == -1.0) & (prev_dir == 1.0)

    return pd.DataFrame(
        {
            "supertrend": supertrend_vals,
            "direction": dir_series,
            "buy": buy.fillna(False).astype(bool),
            "sell": sell.fillna(False).astype(bool),
        },
        index=df.index,
    )


def htf_supertrend_direction(
    df: pd.DataFrame,
    rule: str,
    period: int = 10,
    mult: float = 3.0,
) -> pd.Series:
    """Calculate higher-timeframe Supertrend direction aligned to base bar index.

    Resamples df to rule, computes Supertrend, and aligns to df.index using
    only completed HTF bars (shifted by 1 bar and forward-filled) via align_htf.

    Parameters
    ----------
    df : pd.DataFrame
        Base-timeframe OHLCV DataFrame.
    rule : str
        Resampling rule string (e.g. '4h', '1D').
    period : int, default 10
        Supertrend ATR period on the HTF bars.
    mult : float, default 3.0
        Supertrend multiplier on the HTF bars.

    Returns
    -------
    pd.Series
        HTF direction (+1.0 / -1.0 / NaN) on df.index.
    """
    if df is None or df.empty:
        return pd.Series(index=df.index if df is not None else None, dtype=float, name="direction")

    # Ensure open and volume columns exist so resample_ohlcv succeeds
    df_in = df.copy()
    cols_map = {str(c).lower(): c for c in df_in.columns}
    if "open" not in cols_map and "close" in cols_map:
        df_in["open"] = df_in[cols_map["close"]]
    if "volume" not in cols_map:
        df_in["volume"] = 0.0

    htf_df = resample_ohlcv(df_in, rule)
    if htf_df.empty:
        return pd.Series(np.nan, index=df.index, dtype=float, name="direction")

    htf_st = supertrend(htf_df, period=period, mult=mult)
    aligned = align_htf(htf_st["direction"], df.index, rule)
    if isinstance(aligned, pd.DataFrame):
        res = aligned.iloc[:, 0]
    else:
        res = aligned
    res.name = "direction"
    return res
