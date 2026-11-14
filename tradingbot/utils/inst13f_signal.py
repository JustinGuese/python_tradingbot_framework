"""
Form 13F Institutional Signal & Manager Eligibility Module.

Thesis:
    Concentrated, low-turnover institutional investment managers' top positions
    carry high-conviction information that survives the 45-day SEC filing lag.

Pre-registered Grid of 8:
    1. Universe / Portfolio Size: Top 20 vs Top 30
    2. Scoring Variant: Level (holdings count) vs Level + Change (holdings + net new buys/increases)
    3. Macro Trend Gate: On (QQQ < SMA200 -> cash) vs Off

Pre-registered Manager Eligibility Rule:
    A manager (CIK) is eligible on date `as_of` if and only if:
    - History: >= 8 consecutive quarters of Form 13F filings.
    - Position count: between 10 and 60 positions.
    - Concentration: top-10 holdings weight >= 40% of total portfolio value.
    - Turnover: trailing quarterly turnover < 25%/quarter.

Point-in-Time Discipline:
    All lookups are strictly point-in-time filtered by `filed_at <= as_of`
    (SEC acceptance time). Holdings known only at period-end but not yet
    filed/accepted at `as_of` are invisible to prevent look-ahead bias.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd

cik = int
ticker = str

# Enable subscripting on pd.Series for runtime type introspection compatibility
if not hasattr(pd.Series, "__class_getitem__"):
    pd.Series.__class_getitem__ = classmethod(lambda cls, key: cls)  # type: ignore[attr-defined]


def eligible_filers(
    filings: pd.DataFrame,
    holdings: pd.DataFrame,
    as_of: datetime | date | str,
    params: dict[str, Any] | None = None,
) -> set[cik]:
    """
    Identify institutional managers eligible under pre-registered 13F criteria.

    Point-in-time guard:
        Only filings where `filed_at <= as_of` are considered.

    Eligibility rules:
        - >= 8 consecutive quarters filed.
        - 10-60 positions.
        - Top-10 weight >= 40%.
        - Trailing turnover < 25%/quarter.

    Args:
        filings: DataFrame containing `inst13f_filings` records.
        holdings: DataFrame containing `inst13f_holdings` records.
        as_of: Point-in-time cutoff (SEC acceptance time `filed_at <= as_of`).
        params: Optional dictionary of parameter overrides.

    Returns:
        set[cik]: Set of eligible manager CIKs.

    Raises:
        NotImplementedError: Stub interface for Batch 0; implementation in B1c.
    """
    raise NotImplementedError("eligible_filers stub for B0; to be implemented in B1c")


def conviction_scores(
    filings: pd.DataFrame,
    holdings: pd.DataFrame,
    as_of: datetime | date | str,
    params: dict[str, Any] | None = None,
) -> pd.Series[ticker]:
    """
    Compute conviction scores per ticker from eligible managers' holdings.

    Point-in-time guard:
        Only filings where `filed_at <= as_of` are considered.

    Scoring:
        Base: Count of eligible managers holding the ticker in their top 10.
        Variant: Level + net new buys / increases in latest filing.

    Args:
        filings: DataFrame containing `inst13f_filings` records.
        holdings: DataFrame containing `inst13f_holdings` records.
        as_of: Point-in-time cutoff (SEC acceptance time `filed_at <= as_of`).
        params: Optional dictionary of parameters (e.g. variant, weights).

    Returns:
        pd.Series[ticker]: Series mapping ticker symbols to conviction scores.

    Raises:
        NotImplementedError: Stub interface for Batch 0; implementation in B1c.
    """
    raise NotImplementedError("conviction_scores stub for B0; to be implemented in B1c")
