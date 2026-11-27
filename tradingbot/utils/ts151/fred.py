"""FRED data fetcher with publication lag support for TS151 strategy bots (PLAN-ts151.md S0)."""

from __future__ import annotations

import io
import logging
import os
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

DEFAULT_LAGS: dict[str, int] = {
    "CPIAUCSL": 15,
    "INDPRO": 20,
}


def default_lag(series_id: str) -> int:
    """Return the publication lag in calendar days for a given FRED series ID."""
    if series_id in DEFAULT_LAGS:
        return DEFAULT_LAGS[series_id]
    if series_id.startswith("DGS") or series_id == "T10YIE":
        return 1
    return 1


def fred_series(
    series_id: str,
    lag_days: int | None = None,
    start: str = "1990-01-01",
    cache_dir: Path | str | None = None,
    refresh: bool = False,
) -> pd.Series:
    """Download or load cached public series from FRED with publication lag applied.

    - Downloads public fredgraph.csv (no API key required).
    - Parses values as floats; "." is treated as NaN.
    - Applies publication lag by shifting the DatetimeIndex forward by `lag_days` calendar days.
    - Raises on fetch failure (no synthetic data fallback).
    """
    if lag_days is None:
        lag_days = default_lag(series_id)

    cache_base = Path(cache_dir or os.environ.get("CACHE_DIR", "/tmp"))
    cache_file = cache_base / f"fred_{series_id}.pkl"

    raw_series: pd.Series | None = None

    if not refresh and cache_file.exists():
        try:
            cached = pd.read_pickle(cache_file)
            if isinstance(cached, pd.Series) and not cached.empty:
                raw_series = cached
                logger.debug("Loaded FRED series %s from cache %s", series_id, cache_file)
        except Exception as exc:
            logger.warning("Failed reading FRED cache from %s: %s", cache_file, exc)

    if raw_series is None:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
        try:
            resp = requests.get(url, headers=headers, timeout=20)
        except Exception as exc:
            raise RuntimeError(f"Network error fetching FRED series {series_id}: {exc}") from exc

        if resp.status_code != 200:
            raise RuntimeError(f"FRED returned HTTP {resp.status_code} for series {series_id}")

        try:
            raw_csv = pd.read_csv(io.StringIO(resp.text))
            date_col = "observation_date" if "observation_date" in raw_csv.columns else "DATE"
            val_col = series_id if series_id in raw_csv.columns else raw_csv.columns[1]

            dates = pd.to_datetime(raw_csv[date_col])
            values = pd.to_numeric(raw_csv[val_col].replace(".", float("nan")), errors="coerce")

            raw_series = pd.Series(
                values.to_numpy(),
                index=pd.DatetimeIndex(dates).tz_localize(None).normalize(),
                dtype=float,
            ).dropna()

            if raw_series.empty:
                raise ValueError(f"FRED series {series_id} contains no valid numeric data")
        except Exception as exc:
            raise RuntimeError(f"Failed parsing FRED series {series_id}: {exc}") from exc

        try:
            cache_base.mkdir(parents=True, exist_ok=True)
            raw_series.to_pickle(cache_file)
            logger.debug("Saved FRED series %s to cache %s", series_id, cache_file)
        except Exception as exc:
            logger.warning("Could not write FRED cache to %s: %s", cache_file, exc)

    # Filter start date
    if start is not None:
        start_ts = pd.to_datetime(start).tz_localize(None).normalize()
        raw_series = raw_series[raw_series.index >= start_ts]

    # Apply publication lag by shifting the index forward by lag_days
    lagged_series = raw_series.copy()
    if lag_days > 0:
        lagged_series.index = lagged_series.index + pd.Timedelta(days=lag_days)

    return lagged_series.sort_index()
