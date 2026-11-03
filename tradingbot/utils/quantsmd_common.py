"""Shared helpers for the quants.md (QuantJourney) strategy bots.

Pure pandas, no DB and no network, so every ``tradingbot/quantsmd_*.py`` bot
can import these and keep its logic in a testable ``compute_weights`` function.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator

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


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """Relative strength index of ``close`` over ``window`` bars, in 0..100.

    Computed with ``ta.momentum.RSIIndicator``, the same library call the weekly
    RSI bot uses. The first ``window`` values are NaN.
    """
    return RSIIndicator(close, window=window).rsi()


def rsi_hysteresis(rsi_values: pd.Series, enter_below: float, exit_above: float) -> bool:
    """Return whether an RSI series ends in the held state.

    The state starts flat. Going from flat to held needs a value below
    ``enter_below``; going from held to flat needs a value above ``exit_above``.
    A NaN value resets the state to flat. Returns the final state.
    """
    held = False
    for value in rsi_values:
        if pd.isna(value):
            held = False
        elif not held and value < enter_below:
            held = True
        elif held and value > exit_above:
            held = False
    return held


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20) -> pd.Series:
    """Average true range over ``window`` bars.

    True range is the max of ``high - low``, ``|high - prev_close|`` and
    ``|low - prev_close|``. The first bar uses ``high - low`` alone. The average
    is a simple rolling mean that needs a full window.
    """
    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(window, min_periods=window).mean()


def top_n(scores: pd.Series, n: int, positive_only: bool = False) -> list[str]:
    """Return the ``n`` index labels with the largest scores, largest first.

    NaN scores are dropped. With ``positive_only`` only scores ``> 0`` are kept.
    """
    clean = scores.dropna()
    if positive_only:
        clean = clean[clean > 0]
    ordered = clean.sort_values(ascending=False, kind="stable")
    return [str(label) for label in ordered.index[:n]]


def scale_to_max_gross(weights: dict[str, float], max_gross: float = 1.0) -> dict[str, float]:
    """Drop non-positive weights, then scale so the sum is at most ``max_gross``.

    Entries ``<= 0`` (and NaN) are removed. If the remaining sum exceeds
    ``max_gross``, every weight is multiplied by ``max_gross / sum``.
    """
    kept = {ticker: float(w) for ticker, w in weights.items() if w > 0}
    total = sum(kept.values())
    if total > max_gross:
        factor = max_gross / total
        kept = {ticker: w * factor for ticker, w in kept.items()}
    return kept


def erc_weights(cov: np.ndarray, iters: int = 500) -> np.ndarray:
    """Equal-risk-contribution weights for covariance matrix ``cov``.

    Starts from equal weights and repeatedly moves each weight toward an equal
    share of total risk, averaging old and new weights for damping. Returns
    all-equal weights if the total risk is not positive.
    """
    n = cov.shape[0]
    w = np.full(n, 1.0 / n)
    for _ in range(iters):
        rc = w * (cov @ w)  # risk contributions
        total = rc.sum()
        if total <= 0:
            break
        # A name with non-positive risk contribution (negative covariance) keeps
        # its weight rather than collapsing to zero.
        w_new = np.where(rc > 0, w * (total / n) / np.where(rc > 0, rc, 1.0), w)
        w_new = w_new / w_new.sum()
        w = 0.5 * w + 0.5 * w_new
    return w


def last_rows(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Return the last ``n`` rows of ``df`` (all rows if ``n`` exceeds its length)."""
    return df.iloc[-n:]


ETF3 = ["SPY", "QQQ", "IWM"]
FX4 = ["EURUSD=X", "GBPUSD=X", "AUDUSD=X", "NZDUSD=X"]
FUTS4 = ["MES=F", "MNQ=F", "CL=F", "GC=F"]
