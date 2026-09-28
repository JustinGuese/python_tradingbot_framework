"""
The Cboe volatility-index family: where implied vol sits across the curve.

- ^VIX9D / ^VIX / ^VIX3M / ^VIX6M: 9-day, 30-day, 3-month and 6-month S&P 500
  implied vol. VIX / VIX3M above 1 (backwardation) means the market prices the
  next month as riskier than the next three: the regime where short vol blows up.
- ^VVIX: implied vol of VIX itself, i.e. how uncertain the vol level is.
- ^SKEW: price of S&P 500 tail puts relative to the at-the-money smile.
- ^VXN: the Nasdaq-100 counterpart of VIX.

History on yfinance (checked 2026-09-28): ^VIX and ^SKEW from 1990, ^VXN 2001,
^VIX3M 2006-07, ^VVIX 2007, ^VIX6M 2008, ^VIX9D 2011. A backtest that gates on
one of them can only start where it starts.
"""

import logging
import math
import os
from collections.abc import Callable, Iterable

import pandas as pd

logger = logging.getLogger(__name__)

VIX9D, VIX, VIX3M, VIX6M, VVIX, SKEW, VXN = "^VIX9D", "^VIX", "^VIX3M", "^VIX6M", "^VVIX", "^SKEW", "^VXN"
VOL_INDICES = (VIX9D, VIX, VIX3M, VIX6M, VVIX, SKEW, VXN)


def _finite(x: float | None) -> float | None:
    try:
        x = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and x > 0 else None


def latest_vol_indices(
    get_price: Callable[[str], float], symbols: Iterable[str] = VOL_INDICES
) -> dict[str, float | None]:
    """Latest level of each index via `get_price` (a bot's getLatestPrice).

    A failed or non-positive fetch gives None with a warning rather than raising:
    a bot gating on one index should not die because another was unavailable.
    """
    out: dict[str, float | None] = {}
    for symbol in symbols:
        try:
            out[symbol] = _finite(get_price(symbol))
        except Exception as exc:
            logger.warning("vol index %s unavailable: %s", symbol, exc)
            out[symbol] = None
        if out[symbol] is None:
            logger.warning("vol index %s has no usable level", symbol)
    return out


def term_ratio(front: float | None, back: float | None) -> float | None:
    """front / back, e.g. VIX / VIX3M. Above 1 is backwardation. None if either is missing."""
    front, back = _finite(front), _finite(back)
    if front is None or back is None:
        return None
    return front / back


def vol_index_history(symbols: Iterable[str], start: str = "1990-01-01", cache_dir: str | None = None) -> pd.DataFrame:
    """Daily closes of `symbols` from `start`, one column each (tz-naive dates).

    Cached per day under $RESEARCH_CACHE (default /tmp), for backtests only.
    """
    import yfinance as yf

    symbols = list(symbols)
    cache_dir = cache_dir or os.environ.get("RESEARCH_CACHE", "/tmp")
    tag = "_".join(s.strip("^") for s in symbols)
    path = os.path.join(cache_dir, f"vol_indices_{tag}_{start}_{pd.Timestamp.today():%Y%m%d}.pkl")
    if os.path.exists(path):
        return pd.read_pickle(path)
    columns = {}
    for symbol in symbols:
        data = yf.download(symbol, start=start, auto_adjust=True, progress=False)
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
        close = data["Close"] if len(data) else pd.Series(dtype=float)
        close.index = pd.DatetimeIndex(close.index).tz_localize(None).normalize()
        columns[symbol] = close
    frame = pd.DataFrame(columns).sort_index()
    frame.to_pickle(path)
    return frame
