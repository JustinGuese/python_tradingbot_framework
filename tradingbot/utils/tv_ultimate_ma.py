"""TradingView Ultimate Moving Average (CM_Ultimate_MA_MTF).

Pure pandas, causal (no look-ahead), no DB, no network, and no Bot imports.
Warmup values are NaN / False, never filled with 0.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from tradingbot.utils.tv_core import (
    align_htf,
    ema,
    hma,
    sma,
    t3,
    tema,
    wma,
)

SUPPORTED_KINDS: tuple[str, ...] = ("sma", "ema", "wma", "hma", "tema", "t3")


def _calc_ma(s: pd.Series, kind: str, length: int) -> pd.Series:
    """Compute moving average by kind."""
    if kind == "sma":
        return sma(s, length)
    elif kind == "ema":
        return ema(s, length)
    elif kind == "wma":
        return wma(s, length)
    elif kind == "hma":
        return hma(s, length)
    elif kind == "tema":
        return tema(s, length)
    elif kind == "t3":
        return t3(s, length)
    else:
        raise ValueError(
            f"Unknown moving average kind '{kind}'. Expected one of: {', '.join(SUPPORTED_KINDS)}"
        )


def ultimate_ma(
    close: pd.Series | pd.DataFrame | Any,
    kind: str = "hma",
    length: int = 20,
    slope_bars: int = 1,
    htf_rule: str | None = None,
) -> pd.DataFrame:
    """Calculate Ultimate Moving Average (CM_Ultimate_MA_MTF) and directional turn signals.

    Parameters
    ----------
    close : pd.Series or pd.DataFrame
        Close price Series or OHLCV DataFrame containing a 'close' column.
    kind : str, default "hma"
        Type of moving average: 'sma', 'ema', 'wma', 'hma', 'tema', 't3'.
        Unknown kind raises ValueError.
    length : int, default 20
        MA period length. Must be positive (and >= 2 for 'hma').
    slope_bars : int, default 1
        Lookback distance for slope calculation: ma - ma.shift(slope_bars).
        Must be positive.
    htf_rule : str or None, default None
        Higher timeframe resampling rule (e.g. '4h', '1D'). When specified,
        the MA is built on resampled closes and aligned via align_htf without lookahead.

    Returns
    -------
    pd.DataFrame
        DataFrame with columns ['ma', 'direction', 'turn_up', 'turn_down'] on the base index:
        - ma: Moving average values (float).
        - direction: Sign of slope (+1.0 for rising, -1.0 for falling, 0.0 for flat, NaN during warmup).
        - turn_up: bool flag firing on the bar direction reverses from negative to positive.
        - turn_down: bool flag firing on the bar direction reverses from positive to negative.
    """
    kind_clean = str(kind).lower().strip()
    if kind_clean not in SUPPORTED_KINDS:
        raise ValueError(
            f"Unknown moving average kind '{kind}'. Expected one of: {', '.join(SUPPORTED_KINDS)}"
        )

    if length <= 0:
        raise ValueError(f"length must be positive, got {length}")
    if kind_clean == "hma" and length < 2:
        raise ValueError(f"period length must be at least 2 for hma, got {length}")
    if slope_bars <= 0:
        raise ValueError(f"slope_bars must be positive, got {slope_bars}")

    # Extract clean series and base index
    if isinstance(close, pd.DataFrame):
        base_index = close.index
        cols_map = {str(c).lower(): c for c in close.columns}
        if "close" in cols_map:
            close_s = pd.to_numeric(close[cols_map["close"]].to_numpy(), errors="coerce")
            close_s = pd.Series(close_s, index=base_index, dtype=float)
        elif len(close.columns) > 0:
            close_s = pd.to_numeric(close.iloc[:, 0].to_numpy(), errors="coerce")
            close_s = pd.Series(close_s, index=base_index, dtype=float)
        else:
            close_s = pd.Series(dtype=float, index=base_index)
    elif isinstance(close, pd.Series):
        base_index = close.index
        close_s = pd.to_numeric(close.to_numpy(), errors="coerce")
        close_s = pd.Series(close_s, index=base_index, dtype=float)
    else:
        close_s = pd.Series(pd.to_numeric(close, errors="coerce"), dtype=float)
        base_index = close_s.index

    # Handle tz-aware base index
    if isinstance(base_index, pd.DatetimeIndex) and base_index.tz is not None:
        base_index = base_index.tz_convert(None)
        close_s.index = base_index

    # Empty input handling
    if close_s.empty:
        return pd.DataFrame(
            {
                "ma": pd.Series(dtype=float, index=base_index),
                "direction": pd.Series(dtype=float, index=base_index),
                "turn_up": pd.Series(dtype=bool, index=base_index),
                "turn_down": pd.Series(dtype=bool, index=base_index),
            },
            index=base_index,
        )

    if htf_rule is not None:
        if not isinstance(close_s.index, pd.DatetimeIndex):
            raise ValueError(
                f"htf_rule requires a DatetimeIndex, got {type(close_s.index).__name__}"
            )

        htf_close = close_s.resample(htf_rule).last().dropna()
        if htf_close.empty:
            htf_ma = pd.Series(np.nan, index=htf_close.index, dtype=float)
            htf_direction = pd.Series(np.nan, index=htf_close.index, dtype=float)
        else:
            htf_ma = _calc_ma(htf_close, kind_clean, length)
            htf_diff = htf_ma - htf_ma.shift(slope_bars)
            htf_direction = pd.Series(np.sign(htf_diff), index=htf_ma.index, dtype=float)

        htf_frame = pd.DataFrame(
            {"ma": htf_ma, "direction": htf_direction},
            index=htf_close.index,
        )
        aligned = align_htf(htf_frame, base_index, rule=htf_rule)

        ma = aligned["ma"]
        direction = aligned["direction"]
        prev_direction = direction.shift(1)
        turn_up = ((direction > 0) & (prev_direction < 0)).fillna(False).astype(bool)
        turn_down = ((direction < 0) & (prev_direction > 0)).fillna(False).astype(bool)

        return pd.DataFrame(
            {
                "ma": ma,
                "direction": direction,
                "turn_up": turn_up,
                "turn_down": turn_down,
            },
            index=base_index,
        )

    ma = _calc_ma(close_s, kind_clean, length)
    diff = ma - ma.shift(slope_bars)
    direction = pd.Series(np.sign(diff), index=base_index, dtype=float)
    prev_direction = direction.shift(1)
    turn_up = ((direction > 0) & (prev_direction < 0)).fillna(False).astype(bool)
    turn_down = ((direction < 0) & (prev_direction > 0)).fillna(False).astype(bool)

    return pd.DataFrame(
        {
            "ma": ma,
            "direction": direction,
            "turn_up": turn_up,
            "turn_down": turn_down,
        },
        index=base_index,
    )
