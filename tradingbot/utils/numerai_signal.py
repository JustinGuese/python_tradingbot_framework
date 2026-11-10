"""Numerai Signals signal builder (pure function).

This module implements a 12-1 cross-sectional momentum signal for Numerai Signals.

NOTE: This strategy is UNPROVEN. It serves as a baseline factor model
(12-1 momentum, cross-sectional rank scaled strictly inside (0, 1)) whose
live validation relies on Numerai's tournament scoring.
"""

from __future__ import annotations

import contextlib
import datetime
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd


def _normalize_prices(prices: pd.DataFrame) -> pd.DataFrame:
    """Normalize input prices DataFrame to wide format with DatetimeIndex.

    Handles:
    - MultiIndex column DataFrames (e.g. from yfinance download).
    - Long-form DataFrames with symbol/ticker, date/timestamp, and close/price columns.
    - Standard wide DataFrames (date index, ticker columns).
    """
    if prices.empty:
        return prices.copy()

    df = prices.copy()

    # Extract single level if MultiIndex columns (e.g. ('Close', 'AAPL'))
    if isinstance(df.columns, pd.MultiIndex):
        for level in range(df.columns.nlevels):
            vals_lower = [str(v).lower() for v in df.columns.get_level_values(level)]
            matched_val = None
            for target in ("close", "adj close", "adj_close", "price"):
                if target in vals_lower:
                    idx = vals_lower.index(target)
                    matched_val = df.columns.get_level_values(level)[idx]
                    break
            if matched_val is not None:
                df = df.xs(matched_val, level=level, axis=1)
                break

    # If long format (symbol, timestamp, close)
    cols_lower = {str(c).lower(): c for c in df.columns}
    sym_col = None
    for cand in ("symbol", "ticker"):
        if cand in cols_lower:
            sym_col = cols_lower[cand]
            break

    date_col = None
    for cand in ("date", "timestamp", "datetime"):
        if cand in cols_lower:
            date_col = cols_lower[cand]
            break

    val_col = None
    for cand in ("close", "adj_close", "adj close", "price"):
        if cand in cols_lower:
            val_col = cols_lower[cand]
            break

    if sym_col and date_col and val_col and len(df.columns) <= 10:
        df = df.pivot(index=date_col, columns=sym_col, values=val_col)

    # Convert index to DatetimeIndex if possible
    if not isinstance(df.index, pd.DatetimeIndex):
        with contextlib.suppress(Exception):
            df.index = pd.to_datetime(df.index)

    # Sort ascending and remove duplicate timestamps
    df = df.sort_index()
    if df.index.has_duplicates:
        df = df[~df.index.duplicated(keep="last")]

    return df


def build_signal(
    prices: pd.DataFrame,
    universe: list[str] | Sequence[str],
    asof: Any = None,
    *,
    lookback_bars: int = 252,
    skip_bars: int = 21,
    min_valid: int = 100,
) -> pd.Series:
    """Build a cross-sectional 12-1 momentum signal for Numerai Signals.

    Calculates 12-1 momentum (skipping the most recent month of trading days),
    cross-sectionally ranks the valid universe tickers, and scales them strictly
    inside the interval (0, 1). Tickers with missing history or invalid prices
    receive the median value, so the returned Series contains no NaNs.

    This signal is UNPROVEN and submitted live and unstaked for Numerai scoring.

    Parameters:
        prices: Historical price DataFrame (wide or long format).
        universe: Target list of tickers to generate predictions for.
        asof: Timestamp/date cutoff. Only data at or before this cutoff is used
            (no lookahead). If None, all available rows are used.
        lookback_bars: Total trading days in the momentum window (~252 for 12 months).
        skip_bars: Recent trading days to exclude (~21 for 1 month).
        min_valid: Minimum required number of tickers with valid momentum
            (Numerai Signals requirement, default 100).

    Returns:
        pd.Series indexed by universe with signal values strictly inside (0, 1).

    Raises:
        ValueError: If fewer than `min_valid` tickers have valid momentum values.
    """
    universe_list = list(universe)
    if not universe_list:
        raise ValueError(f"Fewer than {min_valid} tickers have valid values (universe is empty)")

    df = _normalize_prices(prices)

    # Filter by asof cutoff to prevent lookahead
    if asof is not None and not df.empty:
        if (isinstance(asof, datetime.date) and not isinstance(asof, datetime.datetime)) or (
            isinstance(asof, str) and len(asof.strip()) <= 10
        ):
            asof_dt = pd.Timestamp(asof).replace(hour=23, minute=59, second=59, microsecond=999999)
        else:
            asof_dt = pd.to_datetime(asof)

        if isinstance(df.index, pd.DatetimeIndex):
            if df.index.tz is not None:
                asof_dt = asof_dt.tz_localize(df.index.tz) if asof_dt.tz is None else asof_dt.tz_convert(df.index.tz)
            else:
                if asof_dt.tz is not None:
                    asof_dt = asof_dt.tz_localize(None)
            df = df.loc[df.index <= asof_dt]
        else:
            try:
                dt_idx = pd.to_datetime(df.index)
                df = df.loc[dt_idx <= asof_dt]
            except Exception:
                df = df.loc[df.index <= asof]

    if len(df) < lookback_bars:
        raise ValueError(
            f"Fewer than {min_valid} tickers have valid values (history length {len(df)} < lookback {lookback_bars})"
        )

    # Compute 12-1 momentum
    # Start: lookback_bars ago (~12 months)
    # End: skip_bars ago (~1 month)
    start_idx = -1 - lookback_bars if len(df) > lookback_bars else 0
    end_idx = -1 - skip_bars

    p_start = df.iloc[start_idx]
    p_end = df.iloc[end_idx]

    # Evaluate tickers that appear in both universe and prices
    eval_cols = [t for t in universe_list if t in df.columns]
    if not eval_cols:
        raise ValueError(f"Fewer than {min_valid} tickers have valid values (0 matching columns in price data)")

    sub_start = pd.to_numeric(p_start[eval_cols], errors="coerce")
    sub_end = pd.to_numeric(p_end[eval_cols], errors="coerce")

    # Valid if both prices exist and are strictly positive
    valid_mask = (sub_start > 0) & (sub_end > 0) & sub_start.notna() & sub_end.notna()
    valid_start = sub_start[valid_mask]
    valid_end = sub_end[valid_mask]

    raw_momentum = (valid_end / valid_start) - 1.0
    valid_momentum = raw_momentum[np.isfinite(raw_momentum)]

    n_valid = len(valid_momentum)
    if n_valid < min_valid:
        raise ValueError(f"Fewer than {min_valid} tickers have valid values (found {n_valid})")

    # Cross-sectional rank scaled strictly inside (0, 1)
    # Using (rank - 0.5) / n_valid ensures:
    #   min rank 1 -> 0.5 / n_valid > 0
    #   max rank N -> 1.0 - 0.5 / n_valid < 1
    #   median -> exactly 0.5
    ranks = valid_momentum.rank(method="average", ascending=True)
    scaled_ranks = (ranks - 0.5) / n_valid
    median_val = float(scaled_ranks.median())

    # Assign median to tickers with missing history (never NaN)
    signal_values = [scaled_ranks.get(ticker, median_val) for ticker in universe_list]
    signal_series = pd.Series(signal_values, index=universe_list, dtype=float)

    return signal_series
