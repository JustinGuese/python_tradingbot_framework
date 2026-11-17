"""TradingView technical analysis core primitives and HTF alignment helpers.

Pure pandas, causal (no look-ahead), no DB, no network, and no Bot imports.
All warmup values are NaN / False, never filled with 0.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def to_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize a price frame to standard OHLCV format.

    Returns a DataFrame with a tz-naive ascending DatetimeIndex and float columns
    'open', 'high', 'low', 'close', 'volume'.

    Accepts a frame with a 'timestamp' column or a DatetimeIndex. Drops rows with
    NaN close and duplicate timestamps (keeping the last occurrence), and fills
    missing volume with 0.0.
    """
    if frame is None or frame.empty:
        return pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"],
            dtype=float,
            index=pd.DatetimeIndex([], dtype="datetime64[ns]"),
        )

    cols_map = {str(c).lower(): c for c in frame.columns}

    if "timestamp" in cols_map:
        axis = frame[cols_map["timestamp"]]
    else:
        axis = frame.index

    try:
        idx = pd.DatetimeIndex(pd.to_datetime(axis))
    except Exception:
        idx = pd.DatetimeIndex(pd.to_datetime(axis, utc=True))

    if idx.tz is not None:
        idx = idx.tz_convert(None)

    out = pd.DataFrame(index=idx)

    if "close" in cols_map:
        out["close"] = pd.to_numeric(frame[cols_map["close"]].to_numpy(), errors="coerce")
    else:
        out["close"] = np.nan

    for c in ["open", "high", "low"]:
        if c in cols_map:
            out[c] = pd.to_numeric(frame[cols_map[c]].to_numpy(), errors="coerce")
        else:
            out[c] = out["close"]

    if "volume" in cols_map:
        out["volume"] = pd.Series(
            pd.to_numeric(frame[cols_map["volume"]].to_numpy(), errors="coerce"), index=idx
        ).fillna(0.0)
    else:
        out["volume"] = 0.0

    out = out[["open", "high", "low", "close", "volume"]].astype(float)

    # Drops NaN-close rows
    out = out.dropna(subset=["close"])

    # Drops duplicate timestamps (keep last occurrence)
    out = out[~out.index.duplicated(keep="last")]

    # Sorts ascending
    out = out.sort_index()

    return out


def rma(s: pd.Series, n: int) -> pd.Series:
    """Wilder's Running Moving Average (Pine Script ta.rma).

    Equivalent to ewm(alpha=1/n, adjust=False).
    """
    if n <= 0:
        raise ValueError(f"period n must be positive, got {n}")
    if s.empty:
        return pd.Series(dtype=float, index=s.index)
    return s.astype(float).ewm(alpha=1.0 / n, adjust=False).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    """Simple Moving Average (Pine Script ta.sma).

    Warmup rows are NaN.
    """
    if n <= 0:
        raise ValueError(f"period n must be positive, got {n}")
    if s.empty:
        return pd.Series(dtype=float, index=s.index)
    return s.astype(float).rolling(window=n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    """Exponential Moving Average (Pine Script ta.ema).

    Uses alpha = 2 / (n + 1), adjust=False.
    """
    if n <= 0:
        raise ValueError(f"period n must be positive, got {n}")
    if s.empty:
        return pd.Series(dtype=float, index=s.index)
    return s.astype(float).ewm(span=n, adjust=False).mean()


def wma(s: pd.Series, n: int) -> pd.Series:
    """Weighted Moving Average (Pine Script ta.wma).

    Weights are 1, 2, ..., n. Warmup rows are NaN.
    """
    if n <= 0:
        raise ValueError(f"period n must be positive, got {n}")
    if s.empty:
        return pd.Series(dtype=float, index=s.index)
    weights = np.arange(1, n + 1, dtype=float)
    w_sum = weights.sum()
    return s.astype(float).rolling(window=n, min_periods=n).apply(lambda x: np.dot(x, weights) / w_sum, raw=True)


def hma(s: pd.Series, n: int) -> pd.Series:
    """Hull Moving Average (Pine Script ta.hma).

    Formula: wma(2 * wma(s, n // 2) - wma(s, n), floor(sqrt(n))).
    """
    if n <= 1:
        raise ValueError(f"period n must be at least 2, got {n}")
    if s.empty:
        return pd.Series(dtype=float, index=s.index)
    half_length = max(1, n // 2)
    sqrt_length = max(1, int(math.isqrt(n)))
    raw_hma = 2.0 * wma(s, half_length) - wma(s, n)
    return wma(raw_hma, sqrt_length)


def tema(s: pd.Series, n: int) -> pd.Series:
    """Triple Exponential Moving Average (TEMA).

    Formula: 3 * ema1 - 3 * ema2 + ema3.
    """
    if n <= 0:
        raise ValueError(f"period n must be positive, got {n}")
    if s.empty:
        return pd.Series(dtype=float, index=s.index)
    ema1 = ema(s, n)
    ema2 = ema(ema1, n)
    ema3 = ema(ema2, n)
    return 3.0 * ema1 - 3.0 * ema2 + ema3


def _gd(s: pd.Series, n: int, v: float) -> pd.Series:
    e1 = ema(s, n)
    e2 = ema(e1, n)
    return (1.0 + v) * e1 - v * e2


def t3(s: pd.Series, n: int, vfactor: float = 0.7) -> pd.Series:
    """Tillson T3 moving average.

    Recursively applies Generalized DEMA three times with volume factor vfactor.
    """
    if n <= 0:
        raise ValueError(f"period n must be positive, got {n}")
    if s.empty:
        return pd.Series(dtype=float, index=s.index)
    gd1 = _gd(s, n, vfactor)
    gd2 = _gd(gd1, n, vfactor)
    return _gd(gd2, n, vfactor)


def true_range(df: pd.DataFrame) -> pd.Series:
    """True Range matching Pine Script ta.tr.

    tr = max(high - low, abs(high - prev_close), abs(low - prev_close)).
    First bar uses high - low.
    """
    if df.empty:
        return pd.Series(dtype=float, index=df.index)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    tr.name = "true_range"
    return tr


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Average True Range using Wilder RMA smoothing (matches Pine Script ta.atr)."""
    tr = true_range(df)
    res = rma(tr, n)
    res.name = "atr"
    return res


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Relative Strength Index using Wilder smoothing (Pine Script ta.rsi)."""
    if n <= 0:
        raise ValueError(f"period n must be positive, got {n}")
    if len(close) < 2:
        return pd.Series(np.nan, index=close.index, dtype=float)

    diff = close.diff()
    up = diff.clip(lower=0.0)
    down = (-diff).clip(lower=0.0)

    up_rma = rma(up, n)
    down_rma = rma(down, n)

    total = up_rma + down_rma
    res = np.where(total == 0.0, 50.0, 100.0 * (up_rma / total))
    mask_nan = up_rma.isna() | down_rma.isna() | close.isna()
    res = np.where(mask_nan, np.nan, res)
    return pd.Series(res, index=close.index, dtype=float, name="rsi")


def mfi(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Money Flow Index (matches Pine Script ta.mfi).

    Returns NaN where volume is all 0.
    """
    if n <= 0:
        raise ValueError(f"period n must be positive, got {n}")
    if df.empty or "volume" not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)

    vol = df["volume"].astype(float)
    if (vol == 0).all():
        return pd.Series(np.nan, index=df.index, dtype=float)

    tp = (df["high"].astype(float) + df["low"].astype(float) + df["close"].astype(float)) / 3.0
    diff = tp.diff()

    raw_flow = tp * vol
    upper_flow = np.where(diff > 0, raw_flow, 0.0)
    lower_flow = np.where(diff < 0, raw_flow, 0.0)

    upper = pd.Series(upper_flow, index=df.index).rolling(n, min_periods=n).sum()
    lower = pd.Series(lower_flow, index=df.index).rolling(n, min_periods=n).sum()

    total = upper + lower
    res = np.where(total == 0.0, np.nan, 100.0 * (upper / total))
    mask_nan = upper.isna() | lower.isna() | tp.isna()
    res = np.where(mask_nan, np.nan, res)
    return pd.Series(res, index=df.index, dtype=float, name="mfi")


def crossover(a: pd.Series | float | int, b: pd.Series | float | int) -> pd.Series:
    """Strict crossover: True where a crosses above b (a > b and a[1] <= b[1]).

    Returns a boolean Series, strictly False on the first bar and on NaNs.
    """
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        raise TypeError("At least one operand to crossover must be a Series")
    if isinstance(a, (int, float)):
        a = pd.Series(float(a), index=b.index)
    elif isinstance(b, (int, float)):
        b = pd.Series(float(b), index=a.index)

    cond = (a > b) & (a.shift(1) <= b.shift(1))
    return cond.fillna(False).astype(bool)


def crossunder(a: pd.Series | float | int, b: pd.Series | float | int) -> pd.Series:
    """Strict crossunder: True where a crosses below b (a < b and a[1] >= b[1]).

    Returns a boolean Series, strictly False on the first bar and on NaNs.
    """
    return crossover(b, a)


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample OHLCV bars to a higher timeframe rule ('4h', '1D').

    Aggregates open='first', high='max', low='min', close='last', volume='sum'.
    Empty bins are dropped.
    """
    if df.empty:
        return pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"],
            dtype=float,
            index=pd.DatetimeIndex([], dtype="datetime64[ns]"),
        )
    if not isinstance(df.index, pd.DatetimeIndex) or "close" not in df.columns:
        df = to_ohlcv(df)

    resampled = df.resample(rule).agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    resampled = resampled.dropna(subset=["close"])
    return resampled[["open", "high", "low", "close", "volume"]].astype(float)


def align_htf(
    htf: pd.Series | pd.DataFrame,
    base_index: pd.Index | pd.Series | pd.DataFrame,
    rule: str | None = None,
) -> pd.Series | pd.DataFrame:
    """Align higher-timeframe values onto the base index without lookahead.

    Uses only HTF bars that were complete at each base bar by shifting
    one HTF bar, then forward-filling onto base_index.
    """
    if isinstance(base_index, (pd.Series, pd.DataFrame)):
        target_index = base_index.index
    else:
        target_index = base_index

    target_index = pd.DatetimeIndex(target_index)
    if target_index.tz is not None:
        target_index = target_index.tz_convert(None)

    if htf.empty or len(target_index) == 0:
        if isinstance(htf, pd.DataFrame):
            return pd.DataFrame(index=target_index, columns=htf.columns, dtype=float)
        return pd.Series(index=target_index, dtype=float, name=htf.name)

    htf_obj = htf.copy()
    if isinstance(htf_obj.index, pd.DatetimeIndex) and htf_obj.index.tz is not None:
        htf_obj.index = htf_obj.index.tz_convert(None)
    htf_obj = htf_obj.sort_index()

    shifted = htf_obj.shift(1)
    return shifted.reindex(target_index, method="ffill")


def active_weights(flags: dict[str, bool], n_universe: int) -> dict[str, float]:
    """Calculate target weights of 1/n_universe for each active flag."""
    if n_universe <= 0:
        raise ValueError(f"n_universe must be positive, got {n_universe}")
    weight = 1.0 / float(n_universe)
    return {ticker: weight for ticker, active in flags.items() if active}
