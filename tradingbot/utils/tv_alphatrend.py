"""AlphaTrend indicator implementation matching Kıvanç Özbilgiç's Pine Script.

Pure pandas / numpy, causal (no look-ahead), no DB, no network, and no Bot imports.
Warmup rows are NaN / False, never filled with 0.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tradingbot.utils.tv_core import (
    crossover,
    crossunder,
    mfi,
    rsi,
    sma,
    to_ohlcv,
    true_range,
)


def _prepare_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize frame via to_ohlcv, safely handling volume mapping."""
    if df is None or df.empty:
        return to_ohlcv(df)

    cols_map = {str(c).lower(): c for c in df.columns}
    vol_col = cols_map.get("volume")

    if vol_col is not None:
        axis = df[cols_map["timestamp"]] if "timestamp" in cols_map else df.index

        try:
            idx = pd.DatetimeIndex(pd.to_datetime(axis))
        except Exception:
            idx = pd.DatetimeIndex(pd.to_datetime(axis, utc=True))

        if idx.tz is not None:
            idx = idx.tz_convert(None)

        vol_series = pd.Series(
            pd.to_numeric(df[vol_col], errors="coerce").fillna(0.0).to_numpy(),
            index=idx,
        )
        vol_series = vol_series[~vol_series.index.duplicated(keep="last")]

        df_no_vol = df.drop(columns=[vol_col])
        ohlcv = to_ohlcv(df_no_vol)
        if not ohlcv.empty:
            ohlcv["volume"] = vol_series.reindex(ohlcv.index).fillna(0.0).astype(float)
        return ohlcv

    return to_ohlcv(df)


def alphatrend(
    df: pd.DataFrame,
    period: int = 14,
    mult: float = 1.0,
    use_mfi: bool | None = None,
) -> pd.DataFrame:
    """Calculate the AlphaTrend indicator per Kıvanç Özbilgiç's Pine Script.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV price frame (or accepted by `to_ohlcv`).
    period : int, default 14
        Lookback period for ATR and momentum indicator (MFI / RSI).
    mult : float, default 1.0
        ATR multiplier coefficient for trailing bands.
    use_mfi : bool | None, default None
        If None, automatically picks MFI when volume has any non-zero value, else RSI.
        If True, forces MFI. If False, forces RSI.

    Returns
    -------
    pd.DataFrame
        DataFrame on df's normalized index with columns:
        - alphatrend (float): dynamic trailing support/resistance line
        - lagged (float): alphatrend shifted by 2 bars
        - buy (bool): strict crossover of alphatrend above lagged
        - sell (bool): strict crossunder of alphatrend below lagged
        - momentum_used (str): "mfi" or "rsi"
    """
    if period <= 0:
        raise ValueError(f"period must be positive, got {period}")
    if mult <= 0:
        raise ValueError(f"mult must be positive, got {mult}")
    if use_mfi is not None and not isinstance(use_mfi, bool):
        raise ValueError(f"use_mfi must be bool or None, got {use_mfi!r}")

    ohlcv = _prepare_ohlcv(df)

    # Determine momentum indicator type
    if use_mfi is True:
        momentum_type = "mfi"
    elif use_mfi is False:
        momentum_type = "rsi"
    else:
        has_volume = ("volume" in ohlcv.columns) and bool((ohlcv["volume"] > 0).any())
        momentum_type = "mfi" if has_volume else "rsi"

    if ohlcv.empty:
        return pd.DataFrame(
            {
                "alphatrend": pd.Series(dtype=float, index=ohlcv.index),
                "lagged": pd.Series(dtype=float, index=ohlcv.index),
                "buy": pd.Series(dtype=bool, index=ohlcv.index),
                "sell": pd.Series(dtype=bool, index=ohlcv.index),
                "momentum_used": pd.Series(dtype=object, index=ohlcv.index),
            },
            index=ohlcv.index,
        )

    # Momentum series
    mom = mfi(ohlcv, period) if momentum_type == "mfi" else rsi(ohlcv["close"], period)

    # ATR matching Kivanc's Pine: ATR = ta.sma(ta.tr, AP)
    tr = true_range(ohlcv)
    atr_series = sma(tr, period)

    n = len(ohlcv)
    low = ohlcv["low"].to_numpy(dtype=float)
    high = ohlcv["high"].to_numpy(dtype=float)
    atr_arr = atr_series.to_numpy(dtype=float)
    mom_arr = mom.to_numpy(dtype=float)

    upT = low - mult * atr_arr
    downT = high + mult * atr_arr

    at_arr = np.full(n, np.nan, dtype=float)
    prev = np.nan

    for i in range(n):
        up = upT[i]
        dn = downT[i]
        m = mom_arr[i]

        if not np.isfinite(up) or not np.isfinite(dn) or not np.isfinite(m):
            at_arr[i] = np.nan
            prev = np.nan
            continue

        if np.isnan(prev):
            val = up if m >= 50.0 else dn
        else:
            val = (prev if up < prev else up) if m >= 50.0 else (prev if dn > prev else dn)

        at_arr[i] = val
        prev = val

    alphatrend_series = pd.Series(at_arr, index=ohlcv.index, name="alphatrend", dtype=float)
    lagged_series = alphatrend_series.shift(2)
    lagged_series.name = "lagged"

    buy_series = crossover(alphatrend_series, lagged_series)
    buy_series.name = "buy"

    sell_series = crossunder(alphatrend_series, lagged_series)
    sell_series.name = "sell"

    momentum_used_series = pd.Series(momentum_type, index=ohlcv.index, name="momentum_used", dtype=object)

    return pd.DataFrame(
        {
            "alphatrend": alphatrend_series,
            "lagged": lagged_series,
            "buy": buy_series,
            "sell": sell_series,
            "momentum_used": momentum_used_series,
        },
        index=ohlcv.index,
    )
