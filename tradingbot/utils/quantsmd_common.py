"""Shared helpers for the quants.md (QuantJourney) strategy bots.

Pure pandas, no DB and no network, so every ``tradingbot/quantsmd_*.py`` bot
can import these and keep its logic in a testable ``compute_weights`` function.
"""

from __future__ import annotations

import math

import pandas as pd

ETF8 = ["SPY", "EFA", "EEM", "TLT", "IEF", "GLD", "DBC", "VNQ"]
SECTORS9 = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"]

_TRADING_DAYS = 252


def closes_frame(datas: dict[str, pd.DataFrame], tickers: list[str]) -> pd.DataFrame:
    """Build a wide close-price frame from per-ticker bar frames.

    For each ticker, ``datas[ticker]`` is used if present, non-empty and has a
    ``close`` column; otherwise the ticker is skipped. The time axis is the
    ``timestamp`` column when present, else the frame's index. Timestamps are
    made tz-naive, NaNs are dropped and duplicate timestamps keep the last value.

    Returns an empty DataFrame when no ticker yields data, otherwise a frame
    with one column per usable ticker and an ascending index.
    """
    series: list[pd.Series] = []
    found: list[str] = []
    for ticker in tickers:
        frame = datas.get(ticker)
        if frame is None or frame.empty or "close" not in frame.columns:
            continue
        axis = frame["timestamp"] if "timestamp" in frame.columns else frame.index
        index = pd.DatetimeIndex(pd.to_datetime(axis))
        if index.tz is not None:
            index = index.tz_convert(None)
        close = pd.Series(frame["close"].to_numpy(), index=index, dtype=float)
        close = close.dropna()
        close = close[~close.index.duplicated(keep="last")]
        if close.empty:
            continue
        series.append(close)
        found.append(ticker)
    if not series:
        return pd.DataFrame()
    out = pd.concat(series, axis=1, keys=found, sort=False)
    return out.sort_index()


def period_rolled(index: pd.Index, period: str) -> bool:
    """Return True iff the last timestamp starts a new period versus the one before.

    ``period`` is ``"M"`` (calendar month) or ``"Q"`` (calendar quarter).
    Fewer than two timestamps returns False. Any other period raises ValueError.
    """
    if period not in ("M", "Q"):
        raise ValueError(f"unsupported period {period!r}; expected 'M' or 'Q'")
    if len(index) < 2:
        return False
    last = pd.Timestamp(index[-1])
    prev = pd.Timestamp(index[-2])
    if period == "M":
        return (last.year, last.month) != (prev.year, prev.month)
    return (last.year, last.quarter) != (prev.year, prev.quarter)


def is_weekday_bar(index: pd.Index, weekday: int) -> bool:
    """Return True iff the last timestamp falls on ``weekday`` (Mon=0 .. Fri=4).

    An empty index returns False.
    """
    if len(index) == 0:
        return False
    return pd.Timestamp(index[-1]).weekday() == weekday


def equal_weight(active: list[str], cap: float) -> dict[str, float]:
    """Equal weights of ``1/len(active)`` per name, each clipped at ``cap``.

    The clipped excess is not redistributed; it stays in cash. An empty list
    returns ``{}``.
    """
    if not active:
        return {}
    weight = min(1.0 / len(active), cap)
    return dict.fromkeys(active, weight)


def annualized_vol(closes: pd.DataFrame, window: int) -> pd.Series:
    """Annualised volatility per column over the last ``window`` valid returns.

    Uses sample std of ``pct_change`` times ``sqrt(252)``. A column with fewer
    than ``window`` valid returns is NaN.
    """
    returns = closes.pct_change()
    out: dict[str, float] = {}
    for column in closes.columns:
        valid = returns[column].dropna()
        if len(valid) < window:
            out[column] = math.nan
        else:
            out[column] = float(valid.iloc[-window:].std() * math.sqrt(_TRADING_DAYS))
    return pd.Series(out, dtype=float)
