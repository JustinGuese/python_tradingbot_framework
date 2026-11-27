"""Point-in-time bulk data readers for TS151 strategy bots (PLAN-ts151.md S0).

Reads are bulk from PostgreSQL; bots cache the frame in memory and filter by `as_of`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

import pandas as pd

from tradingbot.utils.db import CotPosition, MergerEvent, SecFundamental, get_db_session


def load_fundamentals(
    tickers: Sequence[str] | None = None,
    tags: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Load bulk SEC XBRL fundamentals from the database.

    Returns a DataFrame containing all matching facts across history.
    """
    with get_db_session() as session:
        query = session.query(SecFundamental)
        if tickers is not None:
            query = query.filter(SecFundamental.ticker.in_(list(tickers)))
        if tags is not None:
            query = query.filter(SecFundamental.tag.in_(list(tags)))

        rows = query.all()
        if not rows:
            return pd.DataFrame(
                columns=[
                    "cik",
                    "ticker",
                    "tag",
                    "unit",
                    "period_end",
                    "filed",
                    "value",
                    "form",
                    "fy",
                    "fp",
                ]
            )

        data = [
            {
                "cik": r.cik,
                "ticker": r.ticker,
                "tag": r.tag,
                "unit": r.unit,
                "period_end": pd.to_datetime(r.period_end),
                "filed": pd.to_datetime(r.filed),
                "value": float(r.value),
                "form": r.form,
                "fy": r.fy,
                "fp": r.fp,
            }
            for r in rows
        ]
        return pd.DataFrame(data)


def pit_latest(
    frame: pd.DataFrame,
    as_of: Any,
    max_age_days: int = 450,
) -> pd.DataFrame:
    """Point-in-time latest fundamental fact per (ticker, tag) as of `as_of`.

    Keeps rows with `filed <= as_of` and `period_end >= as_of - max_age_days`.
    Returns a wide DataFrame indexed by ticker with tags as columns.
    """
    if frame.empty:
        return pd.DataFrame()

    as_of_ts = pd.to_datetime(as_of).tz_localize(None).normalize()
    cutoff_ts = as_of_ts - pd.Timedelta(days=max_age_days)

    filed_dates = pd.to_datetime(frame["filed"]).dt.tz_localize(None).dt.normalize()
    period_end_dates = pd.to_datetime(frame["period_end"]).dt.tz_localize(None).dt.normalize()

    mask = (filed_dates <= as_of_ts) & (period_end_dates >= cutoff_ts)
    sub = frame[mask].copy()
    if sub.empty:
        return pd.DataFrame()

    # Sort so the latest filing wins
    sub = sub.sort_values(by=["filed", "period_end"])
    latest_rows = sub.drop_duplicates(subset=["ticker", "tag"], keep="last")

    pivoted = latest_rows.pivot(index="ticker", columns="tag", values="value")
    return pivoted


def pit_quarterly(
    frame: pd.DataFrame,
    ticker: str,
    tag: str,
    as_of: Any,
) -> pd.Series:
    """Point-in-time quarterly series for a given ticker and tag up to `as_of`.

    Keeps only rows filed <= as_of. If multiple filings exist for the same period_end,
    the last filing filed on or before as_of wins.
    Returns a Series indexed by period_end sorted chronologically.
    """
    if frame.empty:
        return pd.Series(dtype=float)

    as_of_ts = pd.to_datetime(as_of).tz_localize(None).normalize()
    filed_dates = pd.to_datetime(frame["filed"]).dt.tz_localize(None).dt.normalize()

    mask = (frame["ticker"] == ticker) & (frame["tag"] == tag) & (filed_dates <= as_of_ts)
    sub = frame[mask].copy()
    if sub.empty:
        return pd.Series(dtype=float)

    # Sort by filing date so the latest filing up to as_of wins per period_end
    sub = sub.sort_values(by="filed")
    deduped = sub.drop_duplicates(subset=["period_end"], keep="last")
    deduped = deduped.sort_values(by="period_end")

    idx = pd.DatetimeIndex(pd.to_datetime(deduped["period_end"]))
    return pd.Series(deduped["value"].astype(float).to_numpy(), index=idx, dtype=float)


def load_cot(codes: Sequence[str] | None = None) -> pd.DataFrame:
    """Load Commitments of Traders (COT) position records from the database."""
    with get_db_session() as session:
        query = session.query(CotPosition)
        if codes is not None:
            query = query.filter(CotPosition.market_code.in_(list(codes)))

        rows = query.all()
        if not rows:
            return pd.DataFrame(
                columns=[
                    "market_code",
                    "report_date",
                    "market_name",
                    "open_interest",
                    "comm_long",
                    "comm_short",
                    "noncomm_long",
                    "noncomm_short",
                    "nonrep_long",
                    "nonrep_short",
                ]
            )

        data = [
            {
                "market_code": r.market_code,
                "report_date": pd.to_datetime(r.report_date),
                "market_name": r.market_name,
                "open_interest": float(r.open_interest),
                "comm_long": float(r.comm_long),
                "comm_short": float(r.comm_short),
                "noncomm_long": float(r.noncomm_long),
                "noncomm_short": float(r.noncomm_short),
                "nonrep_long": float(r.nonrep_long),
                "nonrep_short": float(r.nonrep_short),
            }
            for r in rows
        ]
        return pd.DataFrame(data)


def cot_asof(
    frame: pd.DataFrame,
    as_of: Any,
    release_lag_days: int = 3,
) -> pd.DataFrame:
    """Filter COT positions point-in-time as of `as_of`.

    Keeps rows where `report_date + release_lag_days <= as_of`.
    (Tuesday reports are released Friday with release_lag_days=3).
    """
    if frame.empty:
        return pd.DataFrame(columns=frame.columns)

    as_of_ts = pd.to_datetime(as_of).tz_localize(None).normalize()
    report_dates = pd.to_datetime(frame["report_date"]).dt.tz_localize(None).dt.normalize()

    mask = report_dates + pd.Timedelta(days=release_lag_days) <= as_of_ts
    return frame[mask].copy()


def load_merger_events(start: Any = None) -> pd.DataFrame:
    """Load merger and tender offer filing events from the database."""
    with get_db_session() as session:
        query = session.query(MergerEvent)
        if start is not None:
            start_dt = pd.to_datetime(start)
            query = query.filter(MergerEvent.filed_at >= start_dt)

        rows = query.order_by(MergerEvent.filed_at.asc()).all()
        if not rows:
            return pd.DataFrame(
                columns=[
                    "accession",
                    "cik",
                    "ticker",
                    "company_name",
                    "form",
                    "filed_at",
                    "created_at",
                ]
            )

        data = [
            {
                "accession": r.accession,
                "cik": r.cik,
                "ticker": r.ticker,
                "company_name": r.company_name,
                "form": r.form,
                "filed_at": pd.to_datetime(r.filed_at),
                "created_at": pd.to_datetime(r.created_at) if r.created_at else None,
            }
            for r in rows
        ]
        return pd.DataFrame(data)
