"""
FX carry calculations, FRED short rate data loading, and portfolio weights.

Implements PLAN-carryblend.md S0 shared contract:
- load_fred_rates: Load 3-month short rates from FRED for G10 currencies vs USD with publication lag.
- rate_differentials: Compute foreign short rate minus USD short rate.
- signed_topk_weights: Dollar-neutral long top-k / short bottom-k portfolio weights.
- daily_carry_accrual: Daily carry interest accrual for held portfolio assets.
"""

from __future__ import annotations

import io
import logging
import os
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# 3-Month short rate series from FRED
FRED_RATE_SERIES: dict[str, str] = {
    "EUR": "IR3TIB01EZM156N",
    "GBP": "IR3TIB01GBM156N",
    "AUD": "IR3TIB01AUM156N",
    "NZD": "IR3TIB01NZM156N",
    "JPY": "IR3TIB01JPM156N",
    "CAD": "IR3TIB01CAM156N",
    "CHF": "IR3TIB01CHM156N",
    "NOK": "IR3TIB01NOM156N",
    "SEK": "IR3TIB01SEM156N",
    "USD": "IR3TIB01USM156N",
}

G10_CURRENCIES: list[str] = list(FRED_RATE_SERIES.keys())

# Spot pair mappings vs USD
DIRECT_PAIRS: dict[str, str] = {
    "EURUSD=X": "EUR",
    "GBPUSD=X": "GBP",
    "AUDUSD=X": "AUD",
    "NZDUSD=X": "NZD",
}

INVERT_PAIRS: dict[str, str] = {
    "USDJPY=X": "JPY",
    "USDCAD=X": "CAD",
    "USDCHF=X": "CHF",
    "USDNOK=X": "NOK",
    "USDSEK=X": "SEK",
}

ALL_FX_PAIRS: list[str] = list(DIRECT_PAIRS.keys()) + list(INVERT_PAIRS.keys())


def load_fred_rates(
    lag_days: int = 25,
    cache_dir: Path | str | None = None,
    refresh: bool = False,
    daily_index: pd.DatetimeIndex | None = None,
) -> pd.DataFrame:
    """
    Download or load cached 3-month short interest rates from FRED for G10 currencies:
    EUR, GBP, AUD, NZD, JPY, CAD, CHF, NOK, SEK, USD as decimal rates (e.g. 0.04 for 4%).

    Caches fetched raw monthly rates to a local file in CACHE_DIR or /tmp.
    Raises if fetch fails or any series is missing (no silent drop, no synthetic data).
    Applies lag_days publication lag (shifted by lag_days, default 25).

    Args:
        lag_days: Number of days to lag the rate observations (default 25).
        cache_dir: Optional override for cache directory (default env CACHE_DIR or /tmp).
        refresh: If True, bypass cache and fetch fresh data from FRED.
        daily_index: Optional target DatetimeIndex. If None, uses a daily calendar range
            spanning the data up to the current date.

    Returns:
        pd.DataFrame: Columns EUR, GBP, AUD, NZD, JPY, CAD, CHF, NOK, SEK, USD with decimal rates,
            shifted by lag_days.
    """
    cache_base = Path(cache_dir or os.environ.get("CACHE_DIR", "/tmp"))
    cache_file = cache_base / "fred_fx_rates.pkl"

    raw_rates: pd.DataFrame | None = None

    if not refresh and cache_file.exists():
        try:
            cached = pd.read_pickle(cache_file)
            if isinstance(cached, pd.DataFrame) and all(c in cached.columns for c in G10_CURRENCIES):
                raw_rates = cached[G10_CURRENCIES].sort_index()
                logger.info("Loaded FRED rates from %s", cache_file)
        except Exception as exc:
            logger.warning("Failed reading FRED rate cache from %s: %s", cache_file, exc)

    if raw_rates is None:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        series_data: dict[str, pd.Series] = {}

        for ccy, series_id in FRED_RATE_SERIES.items():
            url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
            try:
                resp = requests.get(url, headers=headers, timeout=20)
            except Exception as exc:
                raise RuntimeError(f"Network error fetching FRED rate series for {ccy} ({series_id}): {exc}") from exc

            if resp.status_code != 200:
                raise RuntimeError(f"FRED returned HTTP {resp.status_code} for {ccy} ({series_id})")

            try:
                raw_csv = pd.read_csv(io.StringIO(resp.text))
                if "observation_date" not in raw_csv.columns or series_id not in raw_csv.columns:
                    raise ValueError(f"Missing expected columns in FRED CSV for {ccy} ({series_id})")

                dates = pd.to_datetime(raw_csv["observation_date"])
                values = pd.to_numeric(raw_csv[series_id], errors="coerce")
                s = pd.Series(
                    values.values,
                    index=pd.DatetimeIndex(dates).tz_localize(None).normalize(),
                ).dropna()

                if s.empty:
                    raise ValueError(f"FRED series for {ccy} ({series_id}) contains no numeric observations")

                # FRED rates are reported in percent (e.g. 4.0 for 4%). Convert to decimal.
                series_data[ccy] = s / 100.0
            except Exception as exc:
                raise RuntimeError(f"Failed parsing FRED series for {ccy} ({series_id}): {exc}") from exc

        raw_rates = pd.DataFrame(series_data).sort_index()

        for ccy in G10_CURRENCIES:
            if ccy not in raw_rates.columns or raw_rates[ccy].dropna().empty:
                raise RuntimeError(f"Missing or empty rate series for {ccy}")

        raw_rates = raw_rates[G10_CURRENCIES]

        try:
            cache_base.mkdir(parents=True, exist_ok=True)
            raw_rates.to_pickle(cache_file)
            logger.info("Saved FRED rate cache to %s", cache_file)
        except Exception as exc:
            logger.warning("Could not write FRED rate cache to %s: %s", cache_file, exc)

    # Align to daily index and forward-fill monthly observations
    if daily_index is not None:
        target_idx = daily_index
        union_idx = target_idx.union(raw_rates.index).sort_values()
        daily_rates = raw_rates.reindex(union_idx).ffill().reindex(target_idx)
    else:
        min_date = raw_rates.index.min()
        max_date = max(raw_rates.index.max(), pd.Timestamp.now().normalize())
        calendar_idx = pd.date_range(start=min_date, end=max_date, freq="D")
        daily_rates = raw_rates.reindex(calendar_idx).ffill()

    daily_rates = daily_rates[G10_CURRENCIES]

    # Apply publication lag (default 25 days)
    if lag_days != 0:
        daily_rates = daily_rates.shift(lag_days)

    return daily_rates


def rate_differentials(rates: pd.DataFrame, ccys: Sequence[str]) -> pd.DataFrame:
    """
    Calculate interest rate differentials: foreign rate minus USD rate for each currency in ccys.

    Args:
        rates: DataFrame with decimal interest rates including 'USD' and all requested 'ccys'.
        ccys: Sequence of currency codes to compute differentials for.

    Returns:
        pd.DataFrame: Columns for each currency in ccys with (r_ccy - r_USD).

    Raises:
        ValueError: If 'USD' is missing or any requested currency is missing from rates.
    """
    if "USD" not in rates.columns:
        raise ValueError("rates DataFrame must contain 'USD' column")

    missing = [c for c in ccys if c not in rates.columns]
    if missing:
        raise ValueError(f"Requested currency/currencies missing from rates: {missing}")

    usd_rate = rates["USD"]
    diffs = pd.DataFrame(index=rates.index)
    for ccy in ccys:
        diffs[ccy] = rates[ccy] - usd_rate
    return diffs


def signed_topk_weights(scores: pd.Series, k: int, gross: float = 1.0) -> dict[str, float]:
    """
    Compute dollar-neutral portfolio weights from scores:
    Long top-k / short bottom-k, each side gross/2, equal weight.
    If fewer than 2*k valid scores, returns {}.

    Args:
        scores: Series mapping asset identifier to score (higher is better).
        k: Number of assets to hold long and short.
        gross: Target gross leverage (default 1.0).

    Returns:
        dict[str, float]: Map of symbol -> weight.
    """
    if k <= 0:
        return {}

    valid = scores.dropna()
    if len(valid) < 2 * k:
        return {}

    sorted_scores = valid.sort_values(ascending=False)
    long_symbols = sorted_scores.index[:k]
    short_symbols = sorted_scores.index[-k:]

    w_long = (gross / 2.0) / k
    w_short = -(gross / 2.0) / k

    weights: dict[str, float] = {}
    for s in long_symbols:
        weights[str(s)] = float(w_long)
    for s in short_symbols:
        weights[str(s)] = float(w_short)

    return weights


def daily_carry_accrual(weights: dict[str, float], diffs_row: pd.Series) -> float:
    """
    Calculate daily carry accrual return for held portfolio assets:
    sum(w_i * diff_i) / 252.

    Args:
        weights: dict of {asset: target_weight}.
        diffs_row: Series of rate differentials for the given date (as decimals).

    Returns:
        float: Daily carry accrual return.

    Raises:
        KeyError: If a held asset with non-zero weight is missing or NaN in diffs_row.
    """
    if not weights:
        return 0.0

    total = 0.0
    for symbol, weight in weights.items():
        if weight == 0.0:
            continue
        if symbol not in diffs_row or pd.isna(diffs_row[symbol]):
            raise KeyError(f"Missing differential for held asset '{symbol}'")
        total += weight * float(diffs_row[symbol])

    return float(total / 252.0)
