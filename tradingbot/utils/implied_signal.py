"""Option-implied signals calculation, ranking, and DB extraction.

Signals:
  - cp_spread: ATM call IV minus ATM put IV (Cremers-Weinbaum 2010).
  - put_skew: 25-delta put IV minus ATM IV.
  - rr25: 25-delta call IV minus 25-delta put IV (Risk Reversal).
  - composite: standardized combination of cp_spread + rr25 - put_skew.

All ranking is cross-sectional and shift-invariant. Names with missing data
are excluded (NaN), never imputed as zero.
"""

from __future__ import annotations

import datetime
import logging
import os
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import pandas as pd
from sqlalchemy import text

logger = logging.getLogger(__name__)


def compute_snapshot_signals(
    quotes_df: pd.DataFrame,
    target_dte: int = 30,
    min_dte: int = 10,
    max_dte: int = 65,
    one_snapshot_per_day: bool = True,
) -> pd.DataFrame:
    """Compute implied volatility signals from a dataframe of option quotes.

    Quotes DataFrame should have columns:
      - underlying (str)
      - snapshot_at (datetime)
      - expiration (datetime)
      - option_type (str: 'C' or 'P', case-insensitive)
      - strike (float)
      - implied_volatility (float, optional)
      - bid (float, optional)
      - ask (float, optional)
      - underlying_price (float, optional)
    """
    if quotes_df.empty:
        return pd.DataFrame(
            columns=[
                "snapshot_at",
                "underlying",
                "spot",
                "cp_spread",
                "put_skew",
                "rr25",
                "call_iv_atm",
                "put_iv_atm",
                "expiry",
                "dte",
            ]
        )

    df = quotes_df.copy()
    df["snapshot_at"] = pd.to_datetime(df["snapshot_at"])
    df["expiration"] = pd.to_datetime(df["expiration"])
    df["option_type"] = df["option_type"].astype(str).str.upper().str[0]
    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")

    # If requested, keep only the latest snapshot per day per underlying
    if one_snapshot_per_day:
        snap_date = df["snapshot_at"].dt.date
        latest_snaps = df.groupby(["underlying", snap_date])["snapshot_at"].transform("max")
        df = df[df["snapshot_at"] == latest_snaps]

    # Effective IV: use implied_volatility if > 0, else mid IV if available
    if "implied_volatility" in df.columns:
        df["iv"] = pd.to_numeric(df["implied_volatility"], errors="coerce")
    else:
        df["iv"] = np.nan

    if "bid" in df.columns and "ask" in df.columns:
        df["bid"] = pd.to_numeric(df["bid"], errors="coerce")
        df["ask"] = pd.to_numeric(df["ask"], errors="coerce")
        df["mid"] = 0.5 * (df["bid"] + df["ask"])
    else:
        df["mid"] = np.nan

    # Group by underlying and snapshot_at
    results = []
    grouped = df.groupby(["underlying", "snapshot_at"])

    for (und, snap_time), group in grouped:
        spot = np.nan
        if "underlying_price" in group.columns:
            valid_px = group["underlying_price"].dropna()
            valid_px = valid_px[valid_px > 0]
            if len(valid_px) > 0:
                spot = float(valid_px.iloc[0])

        if not np.isfinite(spot) or spot <= 0:
            # Fallback: estimate spot from strikes
            spot = float(group["strike"].median())
            if not np.isfinite(spot) or spot <= 0:
                continue

        # Calculate DTE
        expiries = group["expiration"].drop_duplicates()
        dtes = [(exp, (exp - snap_time).total_seconds() / 86400.0) for exp in expiries]
        valid_dtes = [
            (exp, dte)
            for exp, dte in dtes
            if min_dte <= dte <= max_dte
        ]

        if not valid_dtes:
            # If no expiry in range, pick closest positive DTE
            pos_dtes = [(exp, dte) for exp, dte in dtes if dte > 0]
            if not pos_dtes:
                continue
            pos_dtes.sort(key=lambda x: abs(x[1] - target_dte))
            target_exp, target_dte_val = pos_dtes[0]
        else:
            valid_dtes.sort(key=lambda x: abs(x[1] - target_dte))
            target_exp, target_dte_val = valid_dtes[0]

        chain = group[group["expiration"] == target_exp]
        calls = chain[chain["option_type"] == "C"]
        puts = chain[chain["option_type"] == "P"]

        if calls.empty or puts.empty:
            continue

        # ATM strike: strike closest to spot
        strikes = np.unique(chain["strike"].dropna())
        if len(strikes) == 0:
            continue
        atm_strike = float(strikes[np.argmin(np.abs(strikes - spot))])

        atm_call = calls[calls["strike"] == atm_strike]
        atm_put = puts[puts["strike"] == atm_strike]

        if atm_call.empty or atm_put.empty:
            continue

        call_iv_atm = float(atm_call["iv"].dropna().iloc[0]) if not atm_call["iv"].dropna().empty else np.nan
        put_iv_atm = float(atm_put["iv"].dropna().iloc[0]) if not atm_put["iv"].dropna().empty else np.nan

        if not (np.isfinite(call_iv_atm) and np.isfinite(put_iv_atm)):
            continue

        # Cremers-Weinbaum call-put spread
        cp_spread = call_iv_atm - put_iv_atm
        atm_iv = 0.5 * (call_iv_atm + put_iv_atm)

        # 25-delta strikes approx: put ~ 0.95 * S, call ~ 1.05 * S
        put_strikes = np.unique(puts["strike"].dropna())
        call_strikes = np.unique(calls["strike"].dropna())

        put_25d_k = float(put_strikes[np.argmin(np.abs(put_strikes - 0.95 * spot))]) if len(put_strikes) > 0 else atm_strike
        call_25d_k = float(call_strikes[np.argmin(np.abs(call_strikes - 1.05 * spot))]) if len(call_strikes) > 0 else atm_strike

        put_25d_row = puts[puts["strike"] == put_25d_k]
        call_25d_row = calls[calls["strike"] == call_25d_k]

        put_iv_25d = float(put_25d_row["iv"].dropna().iloc[0]) if not put_25d_row["iv"].dropna().empty else put_iv_atm
        call_iv_25d = float(call_25d_row["iv"].dropna().iloc[0]) if not call_25d_row["iv"].dropna().empty else call_iv_atm

        put_skew = put_iv_25d - atm_iv
        rr25 = call_iv_25d - put_iv_25d

        results.append(
            {
                "snapshot_at": snap_time,
                "underlying": und,
                "spot": spot,
                "cp_spread": cp_spread,
                "put_skew": put_skew,
                "rr25": rr25,
                "call_iv_atm": call_iv_atm,
                "put_iv_atm": put_iv_atm,
                "expiry": target_exp,
                "dte": target_dte_val,
            }
        )

    out = pd.DataFrame(results)
    if not out.empty:
        out = out.sort_values(["snapshot_at", "underlying"]).reset_index(drop=True)
    return out


def compute_cross_sectional_signals(
    df: pd.DataFrame,
    signal_name: str = "composite",
    smoothing: int = 5,
) -> pd.DataFrame:
    """Enrich signals with composite score, smoothing, and cross-sectional rank.

    Guarantees:
      - Cross-sectional ranking is invariant to any constant shift applied across names.
      - Missing data names are assigned NaN rank, not zero.
    """
    if df.empty:
        return df.copy()

    res = df.copy()

    # Calculate composite signal per snapshot date
    def _z_by_group(s: pd.Series, grp: pd.Series) -> pd.Series:
        mean = grp.map(s.groupby(grp).mean())
        std = grp.map(s.groupby(grp).std())
        std = std.replace(0.0, np.nan)
        z = (s - mean) / std
        return z.fillna(s - mean)

    if "cp_spread" in res.columns and "rr25" in res.columns and "put_skew" in res.columns:
        z_cps = _z_by_group(res["cp_spread"], res["snapshot_at"])
        z_r25 = _z_by_group(res["rr25"], res["snapshot_at"])
        z_psk = _z_by_group(res["put_skew"], res["snapshot_at"])
        comp = z_cps.fillna(0.0) + z_r25.fillna(0.0) - z_psk.fillna(0.0)
        # Missing data names should have NaN composite
        all_nan = res["cp_spread"].isna() & res["rr25"].isna() & res["put_skew"].isna()
        comp[all_nan] = np.nan
        res["composite"] = comp
    else:
        res["composite"] = res.get("cp_spread", np.nan)

    if signal_name not in res.columns:
        signal_name = "composite"

    # Per-underlying smoothing over time
    if smoothing > 1:
        res = res.sort_values(["underlying", "snapshot_at"]).reset_index(drop=True)
        res[f"{signal_name}_smooth"] = (
            res.groupby("underlying")[signal_name]
            .transform(lambda s: s.rolling(smoothing, min_periods=1).mean())
        )
        active_col = f"{signal_name}_smooth"
    else:
        active_col = signal_name

    # Cross-sectional rank per snapshot_at
    # Missing option data must remain NaN, not 0
    res["rank"] = res.groupby("snapshot_at")[active_col].rank(
        pct=True, ascending=True, na_option="keep"
    )

    return res.sort_values(["snapshot_at", "underlying"]).reset_index(drop=True)


def compute_portfolio_weights(ranks: pd.Series, top_n: int = 5) -> pd.Series:
    """Compute long-only portfolio weights from cross-sectional ranks.

    Allocates 1/top_n to each of the top_n names.
    If fewer than top_n valid names exist, remainder is in cash (sum <= 1.0).
    Names with missing (NaN) data receive 0.0 weight.
    """
    valid = ranks.dropna()
    weights = pd.Series(0.0, index=ranks.index, dtype=float)

    if len(valid) == 0 or top_n <= 0:
        return weights

    # Sort valid descending
    sorted_valid = valid.sort_values(ascending=False)
    winners = sorted_valid.iloc[:top_n].index

    each_weight = 1.0 / float(top_n)
    weights.loc[winners] = each_weight
    return weights


def load_signals_from_db(
    underlyings: Sequence[str],
    start: datetime.date | datetime.datetime,
    end: datetime.date | datetime.datetime,
    cache_dir: str | Path = "/tmp/implied_signal_cache",
    weekly_only: bool = True,
) -> pd.DataFrame:
    """Fetch option quotes from DB per underlying and compute implied signals, caching to disk (pickle/parquet).

    Filters strictly by underlying AND snapshot_at date range to avoid full-table scans.
    """
    from tradingbot.utils.db import get_db_session

    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)

    start_dt = pd.to_datetime(start)
    end_dt = pd.to_datetime(end)

    combined_cache = cache_path / f"implied_signals_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}_{len(underlyings)}.pkl"
    if combined_cache.exists():
        try:
            logger.info("Loading cached signals from %s", combined_cache)
            return pd.read_pickle(combined_cache)
        except Exception as e:
            logger.warning("Failed to load cached pickle %s: %s", combined_cache, e)

    all_signals = []

    for und in sorted(set(underlyings)):
        und_cache = cache_path / f"signals_{und}_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.pkl"
        if und_cache.exists():
            try:
                sig_df = pd.read_pickle(und_cache)
                if not sig_df.empty:
                    all_signals.append(sig_df)
                continue
            except Exception:
                pass

        # Query DB for this underlying
        query_sql = """
            SELECT underlying, contract_symbol, expiration, option_type, strike,
                   bid, ask, implied_volatility, underlying_price, snapshot_at
            FROM option_quotes
            WHERE underlying = :underlying
              AND snapshot_at >= :start_dt
              AND snapshot_at < :end_dt
        """
        if weekly_only:
            # Friday snapshot only (DOW 5)
            query_sql += " AND EXTRACT(DOW FROM snapshot_at) = 5"

        with get_db_session() as session:
            rows = session.execute(
                text(query_sql),
                {"underlying": und, "start_dt": start_dt, "end_dt": end_dt},
            ).mappings().all()

        if not rows:
            continue

        raw_df = pd.DataFrame(rows)
        # Compute signals for this underlying
        sig_df = compute_snapshot_signals(raw_df)
        if not sig_df.empty:
            sig_df.to_pickle(und_cache)
            all_signals.append(sig_df)

    if not all_signals:
        return pd.DataFrame()

    total_df = pd.concat(all_signals, ignore_index=True)
    total_df = total_df.sort_values(["snapshot_at", "underlying"]).reset_index(drop=True)
    try:
        total_df.to_pickle(combined_cache)
    except Exception as e:
        logger.warning("Could not write combined cache: %s", e)

    return total_df
