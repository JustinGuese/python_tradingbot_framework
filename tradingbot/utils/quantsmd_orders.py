"""Daily/intraday bar fill model used by the quants.md order-style bots. Pure functions, no DB.

Every fill is tested against one bar's open/high/low and a trigger price. A fill
returns the fill price, or ``None`` when the order does not fill on that bar.
Stop-limit fills return ``(price, triggered)`` because a triggered order that
does not fill stays live as a limit order.
"""

from __future__ import annotations

import pandas as pd


def fill_limit_buy(o: float, low: float, limit: float) -> float | None:
    """Buy limit: fills if the bar's low reaches ``limit``, at ``min(open, limit)``."""
    if low <= limit:
        return min(o, limit)
    return None


def fill_limit_sell(o: float, h: float, limit: float) -> float | None:
    """Sell limit: fills if the bar's high reaches ``limit``, at ``max(open, limit)``."""
    if h >= limit:
        return max(o, limit)
    return None


def fill_stop_buy(o: float, h: float, stop: float) -> float | None:
    """Buy stop: fills if the bar's high reaches ``stop``, at ``max(open, stop)``."""
    if h >= stop:
        return max(o, stop)
    return None


def fill_stop_sell(o: float, low: float, stop: float) -> float | None:
    """Sell stop: fills if the bar's low reaches ``stop``, at ``min(open, stop)``."""
    if low <= stop:
        return min(o, stop)
    return None


def fill_stop_limit_buy(o: float, h: float, low: float, stop: float, limit: float) -> tuple[float | None, bool]:
    """Buy stop-limit. Returns ``(fill, triggered)``.

    Not triggered (``h < stop``) gives ``(None, False)``. Otherwise the price is
    ``p = max(open, stop)``: ``p <= limit`` fills at ``p``; else ``low <= limit``
    fills at ``limit``; else ``(None, True)``, a live limit that did not fill.
    """
    if h < stop:
        return None, False
    p = max(o, stop)
    if p <= limit:
        return p, True
    if low <= limit:
        return limit, True
    return None, True


def fill_stop_limit_sell(o: float, h: float, low: float, stop: float, limit: float) -> tuple[float | None, bool]:
    """Sell stop-limit. Returns ``(fill, triggered)``.

    Not triggered (``low > stop``) gives ``(None, False)``. Otherwise the price is
    ``p = min(open, stop)``: ``p >= limit`` fills at ``p``; else ``h >= limit``
    fills at ``limit``; else ``(None, True)``, a live limit that did not fill.
    """
    if low > stop:
        return None, False
    p = min(o, stop)
    if p >= limit:
        return p, True
    if h >= limit:
        return limit, True
    return None, True


def bracket_exit(o: float, h: float, low: float, take_profit: float, stop_loss: float) -> tuple[float, str] | None:
    """Exit of a long bracket on one bar. The stop wins a bar that touches both.

    ``low <= stop_loss`` returns ``(min(open, stop_loss), "stop")``; else
    ``h >= take_profit`` returns ``(max(open, take_profit), "target")``; else None.
    """
    if low <= stop_loss:
        return min(o, stop_loss), "stop"
    if h >= take_profit:
        return max(o, take_profit), "target"
    return None


def ohlc_frames(datas: dict[str, pd.DataFrame], tickers: list[str]) -> dict[str, pd.DataFrame]:
    """Per ticker, a frame of ``open, high, low, close`` on a tz-naive ascending index.

    The time axis is the ``timestamp`` column when present, else the frame's index
    (the same rule as ``closes_frame``). NaN rows are dropped and duplicate
    timestamps keep the last row. Tickers with no usable data are omitted.
    """
    out: dict[str, pd.DataFrame] = {}
    columns = ["open", "high", "low", "close"]
    for ticker in tickers:
        frame = datas.get(ticker)
        if frame is None or frame.empty:
            continue
        if any(column not in frame.columns for column in columns):
            continue
        axis = frame["timestamp"] if "timestamp" in frame.columns else frame.index
        index = pd.DatetimeIndex(pd.to_datetime(axis))
        if index.tz is not None:
            index = index.tz_convert(None)
        bars = pd.DataFrame(
            {column: frame[column].to_numpy(dtype=float) for column in columns},
            index=index,
        )
        bars = bars.dropna()
        bars = bars[~bars.index.duplicated(keep="last")]
        if bars.empty:
            continue
        out[ticker] = bars.sort_index()
    return out
