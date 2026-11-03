"""Shared helpers for the quants.md v2 variant bots.

Pure pandas, no DB and no network. The v2 bots add hysteresis, minimum holding
periods, volatility targeting, idle-cash parking and a regime gate on top of the
original quants.md books. These helpers keep that logic in one testable place,
next to the existing ``quantsmd_common`` helpers.
"""

from __future__ import annotations

import math

import pandas as pd

_TRADING_DAYS = 252


def schmitt_state(score: pd.Series, enter_above: float, exit_below: float) -> pd.Series:
    """Hysteresis (Schmitt trigger) state of ``score``, as a bool series.

    The state starts False. It turns True when ``score > enter_above`` and back
    to False when ``score < exit_below``. Values between the two thresholds keep
    the previous state. A NaN score resets the state to False.

    ``exit_below`` must be ``<= enter_above``, otherwise ``ValueError`` is raised.
    The returned series has the same index as ``score``.
    """
    if exit_below > enter_above:
        raise ValueError(f"exit_below ({exit_below}) must be <= enter_above ({enter_above})")
    states: list[bool] = []
    held = False
    for value in score:
        if pd.isna(value):
            held = False
        elif not held and value > enter_above:
            held = True
        elif held and value < exit_below:
            held = False
        states.append(held)
    return pd.Series(states, index=score.index, dtype=bool)


def min_hold_state(raw: pd.Series, min_hold: int) -> pd.Series:
    """Apply a minimum holding period to a bool ``raw`` state series.

    The output follows ``raw`` but may only flip if at least ``min_hold`` bars
    have passed since its last flip. The first bar sets the state to
    ``raw.iloc[0]`` and counts as a flip, so the earliest second flip happens at
    bar ``min_hold``. ``min_hold <= 1`` returns ``raw`` unchanged. Empty input
    returns an empty series.
    """
    if min_hold <= 1 or len(raw) == 0:
        return raw
    values = [bool(v) for v in raw]
    state = values[0]
    last_flip = 0
    out = [state]
    for i in range(1, len(values)):
        if values[i] != state and i - last_flip >= min_hold:
            state = values[i]
            last_flip = i
        out.append(state)
    return pd.Series(out, index=raw.index, dtype=bool)


def vol_target_scale(
    book: dict[str, float],
    closes: pd.DataFrame,
    target_vol: float,
    window: int = 63,
    max_gross: float = 1.0,
) -> dict[str, float]:
    """Scale ``book`` so its realised volatility is near ``target_vol``.

    The book return series is the sum over tickers of ``weight * pct_change`` of
    ``closes``. Tickers missing from ``closes`` are ignored. Realised vol is the
    sample std of the last ``window`` book returns times ``sqrt(252)``. All
    weights are multiplied by ``target_vol / vol``. If the resulting gross
    exposure exceeds ``max_gross``, the weights are rescaled to ``max_gross``.

    ``dict(book)`` is returned unchanged when the book is empty, there are fewer
    than ``window`` book returns, or the vol is NaN or <= 0.
    """
    if not book:
        return dict(book)
    returns = closes.pct_change()
    cols = [ticker for ticker in book if ticker in returns.columns]
    if not cols:
        return dict(book)
    weights = pd.Series({ticker: float(book[ticker]) for ticker in cols})
    book_returns = returns[cols].mul(weights, axis=1).sum(axis=1, min_count=1).dropna()
    if len(book_returns) < window:
        return dict(book)
    vol = float(book_returns.iloc[-window:].std() * math.sqrt(_TRADING_DAYS))
    if math.isnan(vol) or vol <= 0:
        return dict(book)
    factor = target_vol / vol
    scaled = {ticker: float(w) * factor for ticker, w in book.items()}
    gross = sum(scaled.values())
    if gross > max_gross:
        rescale = max_gross / gross
        scaled = {ticker: w * rescale for ticker, w in scaled.items()}
    return scaled


def park_cash(book: dict[str, float], symbol: str = "SHV") -> dict[str, float]:
    """Park uninvested weight in ``symbol``.

    Adds ``1 - sum(book)`` to ``book[symbol]`` when that residual is above
    ``0.005``. Otherwise returns a copy of ``book`` unchanged. A negative residual
    (an over-invested book) never adds a weight.
    """
    out = dict(book)
    residual = 1.0 - sum(book.values())
    if residual > 0.005:
        out[symbol] = out.get(symbol, 0.0) + residual
    return out


def regime_scale(
    closes: pd.DataFrame,
    ref: str = "SPY",
    window: int = 200,
    off_scale: float = 0.0,
) -> float:
    """Return 1.0 when ``ref`` closes above its ``window`` simple moving average.

    Otherwise returns ``off_scale``. This fails open: it returns 1.0 when ``ref``
    is not a column of ``closes`` or there are fewer than ``window`` rows.
    """
    if ref not in closes.columns or len(closes) < window:
        return 1.0
    series = closes[ref]
    last = series.iloc[-1]
    sma = series.rolling(window).mean().iloc[-1]
    if last > sma:
        return 1.0
    return off_scale


def scale_book(book: dict[str, float], factor: float) -> dict[str, float]:
    """Multiply every weight by ``factor``, dropping results that are <= 0."""
    scaled = {ticker: float(w) * factor for ticker, w in book.items()}
    return {ticker: w for ticker, w in scaled.items() if w > 0}
