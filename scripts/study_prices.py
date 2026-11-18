"""
Shared study price-loading and coverage validation utilities.

Provides batch-downloading via yfinance with retries and exponential backoff,
per-symbol parquet disk caching, and universe coverage validation.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

logger = logging.getLogger("study_prices")


def get_default_cache_dir() -> Path:
    """Return default cache directory honoring RETUNE_SCRATCH environment variable."""
    scratch = os.environ.get("RETUNE_SCRATCH")
    if scratch:
        p = Path(scratch)
        p.mkdir(parents=True, exist_ok=True)
        return p
    default_p = Path(".cache/sp500")
    default_p.mkdir(parents=True, exist_ok=True)
    return default_p


def _clean_symbol(symbol: str) -> str:
    return symbol.replace(".", "_").replace("-", "_")


def _standardize_ohlcv_df(raw_df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Standardize single-ticker OHLCV DataFrame."""
    if raw_df.empty:
        return pd.DataFrame()

    df = raw_df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # Convert column names to lowercase strings
    df.columns = [str(c).lower() for c in df.columns]

    if "timestamp" not in df.columns:
        if "date" in df.columns:
            df = df.rename(columns={"date": "timestamp"})
        elif "datetime" in df.columns:
            df = df.rename(columns={"datetime": "timestamp"})
        else:
            df = df.reset_index()
            first_col = str(df.columns[0]).lower()
            if first_col in ("index", "date", "datetime"):
                df = df.rename(columns={df.columns[0]: "timestamp"})
            else:
                df["timestamp"] = df.index

    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
    df = df.dropna(subset=["close" if "close" in df.columns else df.columns[0]])
    if df.empty:
        return pd.DataFrame()

    df["symbol"] = symbol
    for col in ["open", "high", "low", "volume"]:
        if col not in df.columns and "close" in df.columns:
            df[col] = df["close"] if col != "volume" else 0.0

    cols = ["symbol", "timestamp", "open", "high", "low", "close", "volume"]
    available_cols = [c for c in cols if c in df.columns]
    res = df[available_cols].copy()
    res.index = pd.DatetimeIndex(res["timestamp"])
    res = res[~res.index.duplicated(keep="last")].sort_index()
    return res


def load_prices(
    symbols: Iterable[str],
    start: str = "2008-01-01",
    cache_dir: Path | str | None = None,
) -> dict[str, pd.DataFrame]:
    """
    Load OHLCV price DataFrames for given symbols with disk caching.

    Downloads missing symbols in batches of 40 via yf.download(threads=True),
    retries failed batches up to 3 times with backoff, caches per-symbol parquet,
    and logs a WARNING per failed symbol.
    """
    if cache_dir is None:
        c_dir = get_default_cache_dir()
    else:
        c_dir = Path(cache_dir)
        c_dir.mkdir(parents=True, exist_ok=True)

    loaded: dict[str, pd.DataFrame] = {}
    missing_symbols: list[str] = []

    unique_symbols = sorted(set(symbols))

    # 1. Check local parquet cache
    for sym in unique_symbols:
        cache_file = c_dir / f"{_clean_symbol(sym)}.parquet"
        if cache_file.exists():
            try:
                df = pd.read_parquet(cache_file)
                if not df.empty and "close" in df.columns:
                    if "timestamp" in df.columns:
                        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
                        df.index = pd.DatetimeIndex(df["timestamp"])
                    loaded[sym] = df
                    continue
            except Exception as exc:
                logger.debug("Failed reading cache for %s (%s); will refetch", sym, exc)

        missing_symbols.append(sym)

    if not missing_symbols:
        return loaded

    # 2. Batch download missing symbols in chunks of 40
    batch_size = 40
    batches = [missing_symbols[i : i + batch_size] for i in range(0, len(missing_symbols), batch_size)]

    for batch in batches:
        batch_downloaded: dict[str, pd.DataFrame] = {}
        for attempt in range(1, 4):
            try:
                raw = yf.download(
                    batch,
                    start=start,
                    interval="1d",
                    group_by="ticker",
                    threads=True,
                    auto_adjust=True,
                    progress=False,
                )
                if raw is not None and not raw.empty:
                    if len(batch) == 1:
                        sym = batch[0]
                        std_df = _standardize_ohlcv_df(raw, sym)
                        if not std_df.empty:
                            batch_downloaded[sym] = std_df
                    else:
                        for sym in batch:
                            sub_raw = None
                            if isinstance(raw.columns, pd.MultiIndex):
                                if sym in raw.columns.levels[0]:
                                    sub_raw = raw[sym]
                                elif sym in raw.columns.levels[1]:
                                    sub_raw = raw.xs(sym, axis=1, level=1)
                            elif sym in raw.columns:
                                sub_raw = raw[[sym]].rename(columns={sym: "close"})

                            if sub_raw is not None and not sub_raw.empty:
                                std_df = _standardize_ohlcv_df(sub_raw, sym)
                                if not std_df.empty:
                                    batch_downloaded[sym] = std_df
                    break
            except Exception as exc:
                logger.warning("Batch download attempt %d/3 failed for %d symbols: %s", attempt, len(batch), exc)
                if attempt < 3:
                    time.sleep(1.5 * (2**attempt))

        # Save downloaded and check for failures
        for sym in batch:
            df_sym = batch_downloaded.get(sym)
            if df_sym is not None and not df_sym.empty:
                loaded[sym] = df_sym
                cache_file = c_dir / f"{_clean_symbol(sym)}.parquet"
                try:
                    df_sym.to_parquet(cache_file, index=False)
                except Exception as exc:
                    logger.debug("Could not write cache for %s: %s", sym, exc)
            else:
                logger.warning("Failed to obtain price data for symbol: %s", sym)

    return loaded


def require_coverage(
    loaded: dict[str, Any],
    wanted: Iterable[str],
    min_share: float = 0.95,
    label: str = "universe",
) -> None:
    """
    Validate that at least min_share of wanted current universe symbols have >= 1,250 daily bars.

    Raises SystemExit with a clear error message when coverage is insufficient.
    """
    wanted_list = list(wanted)
    total_wanted = len(wanted_list)
    if total_wanted == 0:
        return

    valid_symbols = [
        s
        for s in wanted_list
        if s in loaded and loaded[s] is not None and not getattr(loaded[s], "empty", True) and len(loaded[s]) >= 1250
    ]
    share = len(valid_symbols) / total_wanted

    if share < min_share:
        msg = (
            f"Coverage check failed for {label}: only {len(valid_symbols)}/{total_wanted} "
            f"({share:.1%}) have >= 1,250 daily bars (required >= {min_share:.1%}). "
            "Study aborted without writing document."
        )
        logger.error(msg)
        raise SystemExit(msg)

    logger.info(
        "Coverage check passed for %s: %d/%d (%.1f%%) >= 1,250 daily bars",
        label,
        len(valid_symbols),
        total_wanted,
        share * 100.0,
    )
