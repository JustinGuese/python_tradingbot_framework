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
from itertools import pairwise
from typing import Any

import pandas as pd

cik = int
ticker = str

DEFAULTS: dict[str, Any] = {
    "min_quarters": 8,
    "min_positions": 10,
    "max_positions": 60,
    "top_n": 10,
    "min_top10_weight": 0.40,
    "max_turnover": 0.25,
    "turnover_quarters": 4,
    "variant": "level",
}

# Enable subscripting on pd.Series for runtime type introspection compatibility
if not hasattr(pd.Series, "__class_getitem__"):
    pd.Series.__class_getitem__ = classmethod(lambda cls, key: cls)  # type: ignore[attr-defined]


def _params(params: dict[str, Any] | None) -> dict[str, Any]:
    return {**DEFAULTS, **(params or {})}


def _visible_filings(filings: pd.DataFrame, as_of: Any) -> pd.DataFrame:
    """Filings accepted by as_of; per (cik, period_end) keep the latest accepted
    (an amendment replaces the original only from its own filed_at)."""
    f = filings.copy()
    f["filed_at"] = pd.to_datetime(f["filed_at"])
    f["period_end"] = pd.to_datetime(f["period_end"])
    f = f[f["filed_at"] <= pd.Timestamp(as_of)]
    f = f.sort_values(["cik", "period_end", "filed_at"])
    return f.drop_duplicates(["cik", "period_end"], keep="last")


def _weights(holdings: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """accession -> DataFrame[cusip, ticker, value_usd, weight] (long equity only)."""
    h = holdings
    if "put_call" in h.columns:
        h = h[h["put_call"].isna()]
    h = h[h["value_usd"] > 0]
    out: dict[str, pd.DataFrame] = {}
    for acc, g in h.groupby("accession"):
        g = (
            g.groupby("cusip", as_index=False)
            .agg(ticker=("ticker", "first"), value_usd=("value_usd", "sum"))
            .sort_values("value_usd", ascending=False)
        )
        g["weight"] = g["value_usd"] / g["value_usd"].sum()
        out[acc] = g.reset_index(drop=True)
    return out


def _consecutive_tail(periods: pd.Series) -> list[int]:
    """Indices (into sorted periods) of the trailing run of consecutive quarters."""
    q = [p.year * 4 + (p.month - 1) // 3 for p in periods]
    n = len(q)
    start = n - 1
    while start > 0 and q[start] - q[start - 1] == 1:
        start -= 1
    return list(range(start, n))


def _eligible(filings: pd.DataFrame, holdings: pd.DataFrame, as_of: Any, p: dict[str, Any]) -> dict[int, list[str]]:
    """cik -> accessions (oldest to newest) of the trailing consecutive run, if eligible."""
    vis = _visible_filings(filings, as_of)
    w = _weights(holdings[holdings["accession"].isin(vis["accession"])])
    result: dict[int, list[str]] = {}
    for c, g in vis.groupby("cik"):
        g = g.sort_values("period_end")
        run = _consecutive_tail(g["period_end"])
        if len(run) < p["min_quarters"]:
            continue
        accs = list(g["accession"].iloc[run])
        latest = w.get(accs[-1])
        if latest is None:
            continue
        if not (p["min_positions"] <= len(latest) <= p["max_positions"]):
            continue
        if latest["weight"].head(p["top_n"]).sum() < p["min_top10_weight"]:
            continue
        tail = accs[-(p["turnover_quarters"] + 1) :]
        turns = []
        for a, b in pairwise(tail):
            if a not in w or b not in w:
                turns = []
                break
            wa = w[a].set_index("cusip")["weight"]
            wb = w[b].set_index("cusip")["weight"]
            d = wb.sub(wa, fill_value=0.0).abs().sum() / 2.0
            turns.append(d)
        if not turns or sum(turns) / len(turns) >= p["max_turnover"]:
            continue
        result[c] = accs
    return result


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
    """
    return set(_eligible(filings, holdings, as_of, _params(params)))


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
        Tickers with NULL ticker are dropped; unheld names are absent.
    """
    p = _params(params)
    elig = _eligible(filings, holdings, as_of, p)
    w = _weights(holdings[holdings["accession"].isin({a for v in elig.values() for a in v})])
    scores: dict[str, float] = {}
    for accs in elig.values():
        cur = w[accs[-1]]
        top = cur.head(p["top_n"])
        top = top[top["ticker"].notna()]
        for t in top["ticker"]:
            scores[t] = scores.get(t, 0.0) + 1.0
        if p["variant"] == "level_change" and len(accs) >= 2 and accs[-2] in w:
            prev = w[accs[-2]].set_index("cusip")["value_usd"]
            for row in cur.itertuples():
                if pd.isna(row.ticker):
                    continue
                before = prev.get(row.cusip, 0.0)
                if row.value_usd > before:
                    scores[row.ticker] = scores.get(row.ticker, 0.0) + 1.0
    return pd.Series(scores, dtype=float).sort_values(ascending=False)
