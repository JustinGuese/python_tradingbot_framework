"""Cross-sectional rank-blended factor model for Numerai Signals (S3b).

Combines:
1. Short-term reversal (1-month / 21-bar negative return)
2. Residual momentum (12-1 momentum neutralized to country / market)
3. Buyback authorization event flags (from DB buyback_events)
4. Insider trade event flags (from DB stock_insider_trades)

All features are rank-normalized, combined with long-only non-negative weights,
neutralized to country + market (zero mean per group), and scaled strictly into (0, 1).

Deterministic, DB-only prices, with graceful fallback when event tables are empty/unavailable.
"""

from __future__ import annotations

import logging
import socket
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import numpy as np
import pandas as pd

from tradingbot.utils.numerai_signal import _normalize_prices

logger = logging.getLogger(__name__)

# Default long-only factor weights (weights >= 0, summing to 1.0)
DEFAULT_FACTOR_WEIGHTS: dict[str, float] = {
    "residual_momentum": 0.40,
    "short_term_reversal": 0.30,
    "buyback": 0.15,
    "insider": 0.15,
}

# Bloomberg exchange suffix to country code mapping
EXCHANGE_TO_COUNTRY: dict[str, str] = {
    "US": "US",
    "LN": "GB",
    "GY": "DE",
    "GR": "DE",
    "FP": "FR",
    "PA": "FR",
    "TO": "CA",
    "CN": "CA",
    "JP": "JP",
    "JT": "JP",
    "HK": "HK",
    "AU": "AU",
    "AT": "AU",
    "SW": "CH",
    "VX": "CH",
    "IM": "IT",
    "SM": "ES",
    "NA": "NL",
    "BB": "BE",
}


def infer_country_from_ticker(ticker: str) -> str:
    """Infer country code from yfinance or Numerai ticker symbol.

    Examples:
        'AAPL' -> 'US'
        'BP.L' -> 'GB'
        'BMW.DE' -> 'DE'
        'SHOP.TO' -> 'CA'
        'BMW GY' -> 'DE'
    """
    if not ticker or not isinstance(ticker, str):
        return "US"

    s = ticker.strip()
    # Check if Bloomberg style: 'BMW GY'
    parts = s.split()
    if len(parts) == 2:
        exch = parts[1].upper()
        return EXCHANGE_TO_COUNTRY.get(exch, exch)

    # Check yfinance suffixes
    upper_s = s.upper()
    if upper_s.endswith(".L"):
        return "GB"
    if upper_s.endswith(".DE"):
        return "DE"
    if upper_s.endswith(".TO"):
        return "CA"
    if upper_s.endswith(".PA"):
        return "FR"
    if upper_s.endswith(".AX"):
        return "AU"
    if upper_s.endswith(".T"):
        return "JP"
    if upper_s.endswith(".HK"):
        return "HK"
    if upper_s.endswith(".SW"):
        return "CH"
    if upper_s.endswith(".MI"):
        return "IT"
    if upper_s.endswith(".MC"):
        return "ES"
    if upper_s.endswith(".AS"):
        return "NL"
    if upper_s.endswith(".BR"):
        return "BE"

    return "US"


def build_country_groups(
    universe: Sequence[str],
    country_map: dict[str, str] | None = None,
) -> pd.Series:
    """Build a Series of country codes indexed by universe tickers."""
    mapping = country_map or {}
    groups = {sym: mapping.get(sym) or infer_country_from_ticker(sym) for sym in universe}
    return pd.Series(groups, index=list(universe))


def neutralize(
    series: pd.Series,
    groups: pd.Series | Sequence[str] | dict[str, str] | pd.DataFrame | None = None,
) -> pd.Series:
    """Neutralize a cross-sectional score series by de-meaning per group.

    Guarantees zero mean per group:
        mean_{i in group}(neutralized_i) == 0.0

    If groups is None, neutralizes to the broad market (zero mean overall).
    If groups is a DataFrame, groups by the composite key across columns.
    """
    if series.empty:
        return series.copy()

    s = pd.to_numeric(series, errors="coerce").astype(float).copy()

    if groups is None:
        valid_mean = s.mean()
        return s - (0.0 if np.isnan(valid_mean) else valid_mean)

    if isinstance(groups, pd.DataFrame):
        grp_series = groups.astype(str).agg("_".join, axis=1)
    elif isinstance(groups, dict):
        grp_series = pd.Series(groups)
    elif isinstance(groups, pd.Series):
        grp_series = groups
    else:
        grp_series = pd.Series(list(groups), index=s.index)

    grp_series = grp_series.reindex(s.index).fillna("UNKNOWN")

    # Demean per group
    group_means = s.groupby(grp_series).transform("mean")
    overall_mean = s.mean()
    fallback = 0.0 if np.isnan(overall_mean) else overall_mean
    neutralized = s - group_means.fillna(fallback)
    return neutralized


def rank_normalize(series: pd.Series) -> pd.Series:
    """Cross-sectional rank scaled strictly into the open interval (0, 1).

    Uses (rank - 0.5) / n_valid to ensure:
      - min rank -> 0.5 / n > 0
      - max rank -> 1.0 - 0.5 / n < 1
      - median -> exactly 0.5
      - no NaNs: missing values receive the median (0.5)
    """
    if series.empty:
        return series.copy()

    s = pd.to_numeric(series, errors="coerce")
    valid_mask = s.notna() & np.isfinite(s)
    valid = s[valid_mask]
    n_valid = len(valid)

    out = pd.Series(0.5, index=series.index, dtype=float)
    if n_valid == 0:
        return out

    ranks = valid.rank(method="average", ascending=True)
    scaled = (ranks - 0.5) / n_valid
    out.loc[valid.index] = scaled
    return out


def compute_short_term_reversal(
    prices: pd.DataFrame,
    lookback_bars: int = 21,
) -> pd.Series:
    """Calculate short-term reversal (negative trailing return over lookback_bars).

    Stocks that dropped over the past month receive a high reversal score.
    """
    if len(prices) <= lookback_bars:
        return pd.Series(np.nan, index=prices.columns, dtype=float)

    p_now = pd.to_numeric(prices.iloc[-1], errors="coerce")
    p_past = pd.to_numeric(prices.iloc[-1 - lookback_bars], errors="coerce")

    valid = (p_now > 0) & (p_past > 0) & p_now.notna() & p_past.notna()
    ret = pd.Series(np.nan, index=prices.columns, dtype=float)
    ret[valid] = (p_now[valid] / p_past[valid]) - 1.0

    # Reversal is negative return
    return -ret


def compute_momentum_12_1(
    prices: pd.DataFrame,
    lookback_bars: int = 252,
    skip_bars: int = 21,
) -> pd.Series:
    """Calculate raw 12-1 momentum (return from t-252 to t-21)."""
    if len(prices) < lookback_bars:
        return pd.Series(np.nan, index=prices.columns, dtype=float)

    start_idx = -1 - lookback_bars if len(prices) > lookback_bars else 0
    end_idx = -1 - skip_bars

    p_start = pd.to_numeric(prices.iloc[start_idx], errors="coerce")
    p_end = pd.to_numeric(prices.iloc[end_idx], errors="coerce")

    valid = (p_start > 0) & (p_end > 0) & p_start.notna() & p_end.notna()
    mom = pd.Series(np.nan, index=prices.columns, dtype=float)
    mom[valid] = (p_end[valid] / p_start[valid]) - 1.0
    return mom


def compute_residual_momentum(
    prices: pd.DataFrame,
    lookback_bars: int = 252,
    skip_bars: int = 21,
    groups: Any = None,
) -> pd.Series:
    """Calculate residual (market/group-neutral) momentum."""
    raw_mom = compute_momentum_12_1(prices, lookback_bars=lookback_bars, skip_bars=skip_bars)
    return neutralize(raw_mom, groups=groups)


def _is_db_reachable() -> bool:
    """Fast check whether configured database is reachable without blocking."""
    try:
        from tradingbot.utils.db import DATABASE_URL, engine

        if engine.dialect.name == "sqlite":
            return True

        parsed = urlparse(DATABASE_URL)
        host = parsed.hostname or "localhost"
        port = parsed.port or 5432
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except Exception:
        return False


def load_buyback_flags(
    symbols: Sequence[str],
    since_days: int = 90,
    asof: Any = None,
    session: Any = None,
) -> pd.Series:
    """Load Form 8-K buyback authorization event flags (1.0 = event, 0.0 = none).

    Falls back to 0.0 for all symbols if DB is unreachable, table is empty, or query fails.
    """
    flags = pd.Series(0.0, index=list(symbols), dtype=float)
    if not symbols:
        return flags

    try:
        from tradingbot.utils.db import BuybackEvent, get_db_session

        ref_time = pd.to_datetime(asof) if asof is not None else datetime.now(UTC)
        ref_naive = ref_time.tz_localize(None) if getattr(ref_time, "tzinfo", None) is not None else ref_time
        cutoff = ref_naive - timedelta(days=since_days)

        if session is not None:
            rows = (
                session.query(BuybackEvent.symbol)
                .filter(
                    BuybackEvent.symbol.in_(list(symbols)),
                    BuybackEvent.filed_at >= cutoff,
                    BuybackEvent.filed_at <= ref_naive,
                )
                .all()
            )
            for (sym,) in rows:
                if sym in flags.index:
                    flags[sym] = 1.0
            return flags

        # Check reachability before opening session to prevent hangs
        if not _is_db_reachable():
            return flags

        with get_db_session() as sess:
            rows = (
                sess.query(BuybackEvent.symbol)
                .filter(
                    BuybackEvent.symbol.in_(list(symbols)),
                    BuybackEvent.filed_at >= cutoff,
                    BuybackEvent.filed_at <= ref_naive,
                )
                .all()
            )
            for (sym,) in rows:
                if sym in flags.index:
                    flags[sym] = 1.0
    except Exception as exc:
        logger.debug("Buyback DB query fallback to 0.0: %s", exc)

    return flags


def load_insider_flags(
    symbols: Sequence[str],
    since_days: int = 90,
    asof: Any = None,
    session: Any = None,
) -> pd.Series:
    """Load insider trade flags (1.0 = net buying, -1.0 = net selling, 0.0 = neutral/none).

    Falls back to 0.0 for all symbols if DB is unreachable, table is empty, or query fails.
    """
    flags = pd.Series(0.0, index=list(symbols), dtype=float)
    if not symbols:
        return flags

    try:
        from tradingbot.utils.db import StockInsiderTrade, get_db_session

        ref_time = pd.to_datetime(asof) if asof is not None else datetime.now(UTC)
        ref_naive = ref_time.tz_localize(None) if getattr(ref_time, "tzinfo", None) is not None else ref_time
        cutoff = ref_naive - timedelta(days=since_days)

        def _process_rows(rows: list) -> None:
            net_by_sym: dict[str, float] = {}
            for sym, tx_type, shares, val in rows:
                amt = float(val if val is not None else (shares or 0.0))
                if tx_type and "urchase" in tx_type.casefold():
                    net_by_sym[sym] = net_by_sym.get(sym, 0.0) + amt
                elif tx_type and "ale" in tx_type.casefold():
                    net_by_sym[sym] = net_by_sym.get(sym, 0.0) - amt

            for sym, net in net_by_sym.items():
                if sym in flags.index:
                    if net > 0:
                        flags[sym] = 1.0
                    elif net < 0:
                        flags[sym] = -1.0

        if session is not None:
            rows = (
                session.query(
                    StockInsiderTrade.symbol,
                    StockInsiderTrade.transaction_type,
                    StockInsiderTrade.shares,
                    StockInsiderTrade.value,
                )
                .filter(
                    StockInsiderTrade.symbol.in_(list(symbols)),
                    StockInsiderTrade.transaction_date >= cutoff,
                    StockInsiderTrade.transaction_date <= ref_naive,
                )
                .all()
            )
            _process_rows(rows)
            return flags

        if not _is_db_reachable():
            return flags

        with get_db_session() as sess:
            rows = (
                sess.query(
                    StockInsiderTrade.symbol,
                    StockInsiderTrade.transaction_type,
                    StockInsiderTrade.shares,
                    StockInsiderTrade.value,
                )
                .filter(
                    StockInsiderTrade.symbol.in_(list(symbols)),
                    StockInsiderTrade.transaction_date >= cutoff,
                    StockInsiderTrade.transaction_date <= ref_naive,
                )
                .all()
            )
            _process_rows(rows)
    except Exception as exc:
        logger.debug("Insider DB query fallback to 0.0: %s", exc)

    return flags


def build_rank_blended_signal(
    prices: pd.DataFrame,
    universe: list[str] | Sequence[str],
    asof: Any = None,
    *,
    lookback_bars: int = 252,
    skip_bars: int = 21,
    min_valid: int = 100,
    country_map: dict[str, str] | None = None,
    weights: dict[str, float] | None = None,
    buyback_events: pd.Series | dict[str, float] | None = None,
    insider_trades: pd.Series | dict[str, float] | None = None,
    db_session: Any = None,
) -> pd.Series:
    """Build a cross-sectional rank-blended signal for Numerai Signals.

    Replaces pure 12-1 momentum with a composite of:
      - short-term reversal (skip_bars negative return)
      - residual (market/group-neutral) momentum
      - buyback authorization flags (DB buyback_events)
      - insider trade flags (DB stock_insider_trades)

    Weights are non-negative (NO SHORTS, long-only/flat).
    Composite score is neutralized to country + market and scaled strictly into (0, 1).
    """
    universe_list = list(universe)
    if not universe_list:
        raise ValueError(f"Fewer than {min_valid} tickers have valid values (universe is empty)")

    df = _normalize_prices(prices)

    # Filter by asof cutoff to prevent lookahead
    if asof is not None and not df.empty:
        if (isinstance(asof, datetime) and asof.tzinfo is not None) or isinstance(asof, pd.Timestamp):
            asof_dt = pd.to_datetime(asof)
        elif isinstance(asof, str) and len(asof.strip()) <= 10:
            asof_dt = pd.Timestamp(asof).replace(hour=23, minute=59, second=59, microsecond=999999)
        else:
            asof_dt = pd.to_datetime(asof)

        if isinstance(df.index, pd.DatetimeIndex):
            if df.index.tz is not None:
                asof_dt = asof_dt.tz_localize(df.index.tz) if asof_dt.tz is None else asof_dt.tz_convert(df.index.tz)
            else:
                if getattr(asof_dt, "tz", None) is not None:
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

    # Determine evaluatable columns in universe
    eval_cols = [t for t in universe_list if t in df.columns]
    if not eval_cols:
        raise ValueError(f"Fewer than {min_valid} tickers have valid values (0 matching columns in price data)")

    sub_df = df[eval_cols]

    # Validate weights (must be non-negative)
    blend_weights = dict(DEFAULT_FACTOR_WEIGHTS)
    if weights:
        for k, v in weights.items():
            if v < 0:
                raise ValueError(f"Weight for {k} must be non-negative (found {v})")
        blend_weights.update(weights)

    total_w = sum(blend_weights.values())
    if total_w <= 0:
        raise ValueError("Sum of factor weights must be strictly positive")
    blend_weights = {k: v / total_w for k, v in blend_weights.items()}

    # Country groups for neutralization
    groups = build_country_groups(universe_list, country_map)

    # 1. Short-term reversal (21-bar negative return)
    raw_rev = compute_short_term_reversal(sub_df, lookback_bars=skip_bars)

    # 2. Residual momentum (12-1 momentum neutralized to country/market)
    raw_mom = compute_momentum_12_1(sub_df, lookback_bars=lookback_bars, skip_bars=skip_bars)
    res_mom = neutralize(raw_mom, groups=groups[eval_cols])

    # Check minimum valid price data tickers
    valid_price_mask = raw_mom.notna() & raw_rev.notna()
    n_valid = int(valid_price_mask.sum())
    if n_valid < min_valid:
        raise ValueError(f"Fewer than {min_valid} tickers have valid values (found {n_valid})")

    # 3. Buyback event flags
    if buyback_events is not None:
        buyback_s = pd.Series(buyback_events) if isinstance(buyback_events, dict) else buyback_events
        f_buyback = buyback_s.reindex(universe_list).fillna(0.0)
    else:
        f_buyback = load_buyback_flags(universe_list, since_days=90, asof=asof, session=db_session)

    # 4. Insider trade flags
    if insider_trades is not None:
        insider_s = pd.Series(insider_trades) if isinstance(insider_trades, dict) else insider_trades
        f_insider = insider_s.reindex(universe_list).fillna(0.0)
    else:
        f_insider = load_insider_flags(universe_list, since_days=90, asof=asof, session=db_session)

    # Rank-normalize each feature across the universe
    r_rev = rank_normalize(raw_rev.reindex(universe_list))
    r_mom = rank_normalize(res_mom.reindex(universe_list))
    r_buy = rank_normalize(f_buyback.reindex(universe_list))
    r_ins = rank_normalize(f_insider.reindex(universe_list))

    # Blend rank scores using non-negative weights
    blended = (
        blend_weights["residual_momentum"] * r_mom
        + blend_weights["short_term_reversal"] * r_rev
        + blend_weights["buyback"] * r_buy
        + blend_weights["insider"] * r_ins
    )

    # Neutralize blended score to country + market
    blended_neutral = neutralize(blended, groups=groups)

    # Final rank normalization strictly into (0, 1)
    final_signal = rank_normalize(blended_neutral)

    # Invalidate tickers with missing history -> median 0.5
    valid_tickers = set(raw_mom[valid_price_mask].index)
    for ticker in universe_list:
        if ticker not in valid_tickers:
            final_signal[ticker] = 0.5

    return final_signal.reindex(universe_list)


# Backward-compatible alias
build_signal = build_rank_blended_signal
