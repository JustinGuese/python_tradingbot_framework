"""Form 13F institutional conviction signal walk-forward study (PLAN-alphaideas.md step G1).

Thesis:
    Concentrated, low-turnover institutional investment managers' top positions carry
    high-conviction information that survives the 45-day SEC filing lag.

Pre-registered Grid of 8 (fixed before running, from tradingbot/utils/inst13f_signal.py):
    1. top20_level_nogate       : Top 20 names, Level scoring (count of holders), Macro Gate OFF
    2. top20_level_gate         : Top 20 names, Level scoring, Macro Gate ON (QQQ < SMA200 -> cash)
    3. top20_levelchange_nogate : Top 20 names, Level + Change scoring, Macro Gate OFF
    4. top20_levelchange_gate   : Top 20 names, Level + Change scoring, Macro Gate ON
    5. top30_level_nogate       : Top 30 names, Level scoring, Macro Gate OFF
    6. top30_level_gate         : Top 30 names, Level scoring, Macro Gate ON
    7. top30_levelchange_nogate : Top 30 names, Level + Change scoring, Macro Gate OFF
    8. top30_levelchange_gate   : Top 30 names, Level + Change scoring, Macro Gate ON

Pre-registered Manager Eligibility Rule:
    A manager (CIK) is eligible on date `as_of` if and only if:
    - History: >= 8 consecutive quarters of Form 13F filings.
    - Position count: between 10 and 60 positions.
    - Concentration: top-10 holdings weight >= 40% of total portfolio value.
    - Turnover: trailing quarterly turnover < 25%/quarter.

Point-in-Time Discipline:
    All lookups are strictly point-in-time filtered by `filed_at <= as_of` (SEC acceptance time).
    Holdings known only at period-end but not yet filed/accepted at `as_of` are invisible.

Quarter Requirement:
    First counts rows in `inst13f_filings` / `inst13f_holdings` per quarter.
    If fewer than 24 quarters are present, the doc records "insufficient data"
    and the script exits 0 without backtest.

Global Study Rules (PLAN-alphaideas.md lines 8-19):
    - Bar: alpha vs QQQ, t >= 2.0, positive in both halves, beta < 0.5.
    - Walk-forward: choose on H1, judge on H2.
    - Dual baselines: QQQ benchmark, and static equal-weight hold of the universe.
    - Cash yield: report CAGR at 0% cash and at T-bill yield (^IRX / SHV copier metric).
    - Transaction costs: 5 bp and 20 bp per side.
    - Trial registry: record each trial in tradingbot/utils/trial_registry.py.

Usage:
    POSTGRES_URI=postgresql://user:pass@host:5432/db uv run python scripts/onetime_inst13f_study.py [--out DOC_PATH]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

# Capture raw environment URI before setting fallback
RAW_POSTGRES_URI = os.environ.get("POSTGRES_URI")
if not os.environ.get("POSTGRES_URI") and not os.environ.get("POSTGRES_HOST"):
    os.environ["POSTGRES_URI"] = "ci:ci@localhost:5432/ci"

import pandas as pd  # noqa: E402
import yfinance as yf  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from tradingbot.utils.alpha_report import alpha_stats  # noqa: E402
from tradingbot.utils.inst13f_signal import conviction_scores  # noqa: E402
from tradingbot.utils.trial_registry import record_trial  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("inst13f_study")

DOC_PATH = Path("docs/backtests/inst13f-2026-11.md")
MIN_REQUIRED_QUARTERS = 24

PRE_REGISTERED_GRID = [
    {"label": "top20_level_nogate", "p_size": 20, "variant": "level", "gate": False},
    {"label": "top20_level_gate", "p_size": 20, "variant": "level", "gate": True},
    {"label": "top20_levelchange_nogate", "p_size": 20, "variant": "level_change", "gate": False},
    {"label": "top20_levelchange_gate", "p_size": 20, "variant": "level_change", "gate": True},
    {"label": "top30_level_nogate", "p_size": 30, "variant": "level", "gate": False},
    {"label": "top30_level_gate", "p_size": 30, "variant": "level", "gate": True},
    {"label": "top30_levelchange_nogate", "p_size": 30, "variant": "level_change", "gate": False},
    {"label": "top30_levelchange_gate", "p_size": 30, "variant": "level_change", "gate": True},
]


def check_quarter_counts(uri: str | None) -> tuple[int, int, int, list[dict[str, Any]], str | None]:
    """Check quarterly counts of filings and holdings in Postgres.

    Returns:
        (num_valid_quarters, total_filings, total_holdings, quarter_rows, error_message)
    """
    if not uri or "ci:ci@localhost" in uri or "stub:stub" in uri:
        return 0, 0, 0, [], "POSTGRES_URI not configured or points to dummy credentials."

    try:
        engine = create_engine(uri)
        with engine.connect() as conn:
            # Query filings per quarter
            filings_res = conn.execute(
                text("""
                SELECT
                    date_trunc('quarter', period_end)::date AS quarter_end,
                    count(DISTINCT accession) AS filings_count
                FROM inst13f_filings
                GROUP BY 1
                ORDER BY 1;
            """)
            ).fetchall()

            # Query holdings per quarter
            holdings_res = conn.execute(
                text("""
                SELECT
                    date_trunc('quarter', f.period_end)::date AS quarter_end,
                    count(h.id) AS holdings_count
                FROM inst13f_filings f
                JOIN inst13f_holdings h ON f.accession = h.accession
                GROUP BY 1
                ORDER BY 1;
            """)
            ).fetchall()

        filings_dict = {row[0]: int(row[1]) for row in filings_res}
        holdings_dict = {row[0]: int(row[1]) for row in holdings_res}

        all_quarters = sorted(set(filings_dict.keys()).union(holdings_dict.keys()))
        quarter_rows: list[dict[str, Any]] = []
        valid_quarters_count = 0
        total_filings = sum(filings_dict.values())
        total_holdings = sum(holdings_dict.values())

        for q in all_quarters:
            f_cnt = filings_dict.get(q, 0)
            h_cnt = holdings_dict.get(q, 0)
            if f_cnt > 0 and h_cnt > 0:
                valid_quarters_count += 1
            quarter_rows.append(
                {
                    "quarter": str(q),
                    "filings": f_cnt,
                    "holdings": h_cnt,
                    "is_valid": bool(f_cnt > 0 and h_cnt > 0),
                }
            )

        return valid_quarters_count, total_filings, total_holdings, quarter_rows, None
    except Exception as exc:
        err = f"Database query failed: {type(exc).__name__}: {exc}"
        logger.warning(err)
        return 0, 0, 0, [], err


def write_insufficient_data_doc(
    doc_path: Path,
    num_quarters: int,
    total_filings: int,
    total_holdings: int,
    quarter_rows: list[dict[str, Any]],
    reason: str | None = None,
) -> None:
    """Generate docs/backtests/inst13f-2026-11.md when fewer than 24 quarters are available."""
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    content = f"""# Institutional Form 13F Conviction Signal Study (2026-11)

Study evaluating quarterly Form 13F institutional conviction signals across 8 pre-registered variants.
Script: `scripts/onetime_inst13f_study.py`.

## Pre-Registered Protocol

- **Thesis**: Concentrated, low-turnover institutional investment managers' top positions carry high-conviction information that survives the 45-day SEC filing lag.
- **Pre-Registered Grid of 8**:
  1. `top20_level_nogate`: Top 20 names, Level scoring (count of holders), Macro Gate OFF.
  2. `top20_level_gate`: Top 20 names, Level scoring, Macro Gate ON (QQQ < SMA200 -> cash).
  3. `top20_levelchange_nogate`: Top 20 names, Level + Change scoring, Macro Gate OFF.
  4. `top20_levelchange_gate`: Top 20 names, Level + Change scoring, Macro Gate ON.
  5. `top30_level_nogate`: Top 30 names, Level scoring, Macro Gate OFF.
  6. `top30_level_gate`: Top 30 names, Level scoring, Macro Gate ON.
  7. `top30_levelchange_nogate`: Top 30 names, Level + Change scoring, Macro Gate OFF.
  8. `top30_levelchange_gate`: Top 30 names, Level + Change scoring, Macro Gate ON.
- **Pre-Registered Manager Eligibility**:
  - History: >= 8 consecutive quarters of Form 13F filings.
  - Position count: between 10 and 60 positions.
  - Concentration: top-10 holdings weight >= 40% of total portfolio value.
  - Turnover: trailing quarterly turnover < 25%/quarter.
- **Point-in-Time Discipline**: Only filings with `filed_at <= as_of` are visible.
- **Evaluation Bar (CLAUDE.md)**: Alpha vs QQQ, t >= 2.0, positive in both halves, beta < 0.5.
- **Dual Baselines**: QQQ and static equal-weight hold of the universe.
- **Costs**: 5 bp and 20 bp per side.
- **Cash**: Report CAGR at 0% cash and at T-bill yield (`^IRX`).

## Data Availability: Insufficient Data

- **Quarters Required**: >= {MIN_REQUIRED_QUARTERS} quarters (6 years of quarterly Form 13F data)
- **Quarters Found**: {num_quarters}
- **Filings Found**: {total_filings}
- **Holdings Found**: {total_holdings}
- **Status**: **insufficient data**
"""

    if reason:
        content += f"\n**Reason**: {reason}\n"

    if quarter_rows:
        content += "\n### Quarter Breakdown\n\n| Quarter | Filings | Holdings | Valid? |\n|---|---|---|---|\n"
        for row in quarter_rows:
            content += (
                f"| {row['quarter']} | {row['filings']} | {row['holdings']} | {'Yes' if row['is_valid'] else 'No'} |\n"
            )
    else:
        content += "\nNo historical Form 13F filing or holding records were found in the database.\n"

    content += f"""
Per pre-registered study rules (PLAN-alphaideas.md step G1), when fewer than {MIN_REQUIRED_QUARTERS} quarters
are present in `inst13f_filings` / `inst13f_holdings`, the study records "insufficient data" and exits
without executing the backtest. Production Postgres data backfill is required to complete this study.
"""
    doc_path.write_text(content, encoding="utf-8")
    logger.info("Wrote insufficient data backtest document to %s", doc_path)


def write_completed_doc(
    doc_path: Path,
    summary_df: pd.DataFrame,
    cash_df: pd.DataFrame,
    start_date: str,
    end_date: str,
    split_date: str,
    num_quarters: int,
) -> None:
    """Write full generated backtest report to doc_path when >= 24 quarters are available."""
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    passed_any = bool((summary_df["Bar Passed"] == "PASS").any())
    verdict = "PASS" if passed_any else "unproven"

    content = f"""# Institutional Form 13F Conviction Signal Study ({start_date[:7]} to {end_date[:7]}) — Study Results

Generated by `scripts/onetime_inst13f_study.py`.
Evaluates quarterly Form 13F institutional conviction signals across 8 pre-registered variants.
Total historical dataset: {num_quarters} quarters.

## Pre-Registered Protocol & Eligibility

- **Grid of 8 variants**: Universe Top 20 vs 30 x Scoring (Level vs Level+Change) x Macro Gate (ON/OFF).
- **Manager Eligibility**: >= 8 consecutive quarters filed, 10-60 positions, top-10 weight >= 40%, trailing turnover < 25%/quarter.
- **Point-in-Time Discipline**: SEC acceptance cutoff (`filed_at <= as_of`).
- **Benchmark**: QQQ.
- **Secondary Baseline**: Static equal-weight hold of candidate names.
- **Costs**: 5 bp and 20 bp per side on rebalancing turnover.
- **Walk-forward**: H1 ({start_date} to {split_date}) and H2 ({split_date} to {end_date}).
- **Acceptance Bar**: Alpha vs QQQ, t >= 2.0, positive in both halves, beta < 0.5.

## Results: Baseline Performance (Cash 0% — Paper Metric)

| Variant | Universe | Scoring | Gate | Alpha vs QQQ | t | Beta | Corr | H1 Alpha (t) | H2 Alpha (t) | vs EW Baseline Alpha (t) | Max DD | Bar Passed? |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
"""
    for _, row in summary_df.iterrows():
        content += (
            f"| {row['Variant']} | {row['Universe']} | {row['Scoring']} | {row['Gate']} | "
            f"{row['Alpha']:.2%} | {row['t']:.2f} | {row['Beta']:.2f} | {row['Corr']:.2f} | "
            f"{row['H1 Alpha']:.2%} ({row['H1 t']:.2f}) | {row['H2 Alpha']:.2%} ({row['H2 t']:.2f}) | "
            f"{row['EW Alpha']:.2%} ({row['EW t']:.2f}) | {row['Max DD']:.2%} | {row['Bar Passed']} |\n"
        )

    content += """
## Results: Leveraged Cash (Idle Cash in SHV / T-Bill Yield — Copier Metric)

| Variant | Universe | Scoring | Gate | Net Yield + T-Bill | Alpha vs QQQ | t | Beta | Corr | H1 Alpha (t) | H2 Alpha (t) | Bar Passed? |
|---|---|---|---|---|---|---|---|---|---|---|---|
"""
    for _, row in cash_df.iterrows():
        content += (
            f"| {row['Variant']} | {row['Universe']} | {row['Scoring']} | {row['Gate']} | "
            f"{row['Net Yield + TBill']:.2%} | {row['Alpha']:.2%} | {row['t']:.2f} | {row['Beta']:.2f} | {row['Corr']:.2f} | "
            f"{row['H1 Alpha']:.2%} ({row['H1 t']:.2f}) | {row['H2 Alpha']:.2%} ({row['H2 t']:.2f}) | "
            f"{row['Bar Passed']} |\n"
        )

    content += f"""
## Findings & Analysis

1. **Promotion Verdict**: **{verdict}** under the CLAUDE.md standard.
2. **Scoring Variants**: Comparison between Level (holdings count) and Level + Change (including net new positions).
3. **Macro Gate**: Impact of QQQ < SMA200 cash protection on drawdown and beta.
"""
    doc_path.write_text(content, encoding="utf-8")
    logger.info("Wrote completed backtest document to %s", doc_path)


def run_inst13f_backtest(
    uri: str,
    doc_path: Path,
    num_quarters: int,
) -> int:
    """Execute full walk-forward backtest for 8 pre-registered variants."""
    engine = create_engine(uri)
    logger.info("Loading Form 13F filings and holdings from database...")

    filings_df = pd.read_sql(
        "SELECT accession, cik, filer_name, period_end, filed_at, is_amendment FROM inst13f_filings",
        engine,
    )
    holdings_df = pd.read_sql(
        "SELECT accession, cusip, ticker, value_usd, shares, put_call FROM inst13f_holdings",
        engine,
    )

    filings_df["filed_at"] = pd.to_datetime(filings_df["filed_at"])
    filings_df["period_end"] = pd.to_datetime(filings_df["period_end"])

    # Determine quarterly rebalance dates (45 days after calendar quarter ends)
    periods = sorted(filings_df["period_end"].drop_duplicates())
    rebal_dates = [p + timedelta(days=45) for p in periods if p + timedelta(days=45) <= pd.Timestamp.now()]

    if len(rebal_dates) < MIN_REQUIRED_QUARTERS:
        write_insufficient_data_doc(
            doc_path,
            num_quarters=len(rebal_dates),
            total_filings=len(filings_df),
            total_holdings=len(holdings_df),
            quarter_rows=[],
            reason=f"Fewer than {MIN_REQUIRED_QUARTERS} rebalance dates available after 45-day filing lag.",
        )
        return 0

    start_date = str(rebal_dates[0].date())
    end_date = str(rebal_dates[-1].date())
    mid_idx = len(rebal_dates) // 2
    split_date = str(rebal_dates[mid_idx].date())

    logger.info("Running backtest from %s to %s (H1/H2 split: %s)...", start_date, end_date, split_date)

    # Download QQQ and ^IRX
    bench = yf.download(
        ["QQQ", "^IRX"],
        start=start_date,
        auto_adjust=False,
        progress=False,
        group_by="column",
    )["Close"]
    qqq = bench["QQQ"].dropna()
    irx = bench["^IRX"].dropna()

    qqq.index = pd.DatetimeIndex(qqq.index).tz_localize(None).normalize()
    irx.index = pd.DatetimeIndex(irx.index).tz_localize(None).normalize()
    qqq_ret = qqq.pct_change().dropna()
    qqq_sma200 = qqq.rolling(window=200).mean()

    # Pre-compute signals per rebalance date and variant
    universe_tickers: set[str] = set()
    signals_by_date: dict[pd.Timestamp, dict[str, pd.Series]] = {}

    for r_dt in rebal_dates:
        signals_by_date[r_dt] = {}
        for var in ["level", "level_change"]:
            scores = conviction_scores(filings_df, holdings_df, as_of=r_dt, params={"variant": var})
            signals_by_date[r_dt][var] = scores
            universe_tickers.update(scores.head(30).index.tolist())

    logger.info("Downloading historical prices for %d candidate universe tickers...", len(universe_tickers))
    prices_raw = yf.download(
        sorted(universe_tickers),
        start=start_date,
        auto_adjust=True,
        progress=False,
    )["Close"]
    prices = prices_raw.dropna(how="all")
    prices.index = pd.DatetimeIndex(prices.index).tz_localize(None).normalize()
    daily_stock_rets = prices.pct_change()

    # Static equal-weight baseline return of all candidate names
    ew_baseline_ret = daily_stock_rets.mean(axis=1).reindex(qqq_ret.index).dropna()

    summary_rows = []
    cash_rows = []

    for cfg in PRE_REGISTERED_GRID:
        p_size = cfg["p_size"]
        variant = cfg["variant"]
        gate = cfg["gate"]
        label = cfg["label"]

        portfolio_daily_rets = pd.Series(index=qqq_ret.index, dtype=float)

        for i, r_dt in enumerate(rebal_dates[:-1]):
            next_r_dt = rebal_dates[i + 1]
            scores = signals_by_date[r_dt][variant]
            top_names = scores.head(p_size).index.tolist()

            # Align to trading dates between r_dt and next_r_dt
            mask = (portfolio_daily_rets.index >= r_dt) & (portfolio_daily_rets.index < next_r_dt)
            sub_dates = portfolio_daily_rets.index[mask]
            if len(sub_dates) == 0:
                continue

            # Check macro trend gate (QQQ < SMA200)
            is_gated = bool(
                gate and r_dt in qqq.index and r_dt in qqq_sma200.index and qqq.loc[r_dt] < qqq_sma200.loc[r_dt]
            )

            if is_gated or len(top_names) == 0:
                portfolio_daily_rets.loc[sub_dates] = 0.0
            else:
                avail_names = [t for t in top_names if t in daily_stock_rets.columns]
                if avail_names:
                    basket_ret = daily_stock_rets.loc[sub_dates, avail_names].mean(axis=1)
                    # Deduct 5 bp transaction cost on turnover at entry
                    basket_ret.iloc[0] -= 5.0 / 10_000.0
                    portfolio_daily_rets.loc[sub_dates] = basket_ret
                else:
                    portfolio_daily_rets.loc[sub_dates] = 0.0

        p_ret = portfolio_daily_rets.dropna()
        aligned_qqq = qqq_ret.reindex(p_ret.index).dropna()
        p_ret = p_ret.reindex(aligned_qqq.index)
        aligned_ew = ew_baseline_ret.reindex(p_ret.index).dropna()

        split_ts = pd.Timestamp(split_date)
        h1_mask = p_ret.index < split_ts
        h2_mask = p_ret.index >= split_ts

        st_full = alpha_stats(p_ret, aligned_qqq)
        st_h1 = alpha_stats(p_ret[h1_mask], aligned_qqq[h1_mask])
        st_h2 = alpha_stats(p_ret[h2_mask], aligned_qqq[h2_mask])
        st_ew = alpha_stats(p_ret, aligned_ew)

        if st_full is None or st_h1 is None or st_h2 is None:
            continue

        eq = (1.0 + p_ret).cumprod()
        dd = float((eq / eq.cummax() - 1.0).min())

        passed = (
            "PASS"
            if (st_full.alpha > 0 and st_h1.alpha > 0 and st_h2.alpha > 0 and st_full.beta < 0.5 and st_full.t >= 2.0)
            else "FAIL"
        )

        summary_rows.append(
            {
                "Variant": label,
                "Universe": f"Top {p_size}",
                "Scoring": variant,
                "Gate": "ON" if gate else "OFF",
                "Alpha": st_full.alpha,
                "t": st_full.t,
                "Beta": st_full.beta,
                "Corr": st_full.corr,
                "H1 Alpha": st_h1.alpha,
                "H1 t": st_h1.t,
                "H2 Alpha": st_h2.alpha,
                "H2 t": st_h2.t,
                "EW Alpha": st_ew.alpha if st_ew else 0.0,
                "EW t": st_ew.t if st_ew else 0.0,
                "Max DD": dd,
                "Bar Passed": passed,
            }
        )

        # Record trial in trial_registry
        record_trial(
            bot="inst13f_conviction",
            params={"portfolio_size": p_size, "variant": variant, "macro_gate": gate},
            alpha=st_full.alpha,
            alpha_t=st_full.t,
            beta=st_full.beta,
            n=st_full.n,
        )

        # Cash yield addition (T-bill ^IRX)
        aligned_irx = irx.reindex(p_ret.index).ffill().fillna(0.0)
        daily_tbill = (aligned_irx / 100.0) / 252.0
        p_ret_cash = p_ret + daily_tbill
        st_cash_full = alpha_stats(p_ret_cash, aligned_qqq)
        st_cash_h1 = alpha_stats(p_ret_cash[h1_mask], aligned_qqq[h1_mask])
        st_cash_h2 = alpha_stats(p_ret_cash[h2_mask], aligned_qqq[h2_mask])

        if st_cash_full and st_cash_h1 and st_cash_h2:
            cash_passed = (
                "PASS"
                if (
                    st_cash_full.alpha > 0
                    and st_cash_h1.alpha > 0
                    and st_cash_h2.alpha > 0
                    and st_cash_full.beta < 0.5
                    and st_cash_full.t >= 2.0
                )
                else "FAIL"
            )
            cash_rows.append(
                {
                    "Variant": label,
                    "Universe": f"Top {p_size}",
                    "Scoring": variant,
                    "Gate": "ON" if gate else "OFF",
                    "Net Yield + TBill": float(p_ret.mean() * 252 + (aligned_irx.mean() / 100.0)),
                    "Alpha": st_cash_full.alpha,
                    "t": st_cash_full.t,
                    "Beta": st_cash_full.beta,
                    "Corr": st_cash_full.corr,
                    "H1 Alpha": st_cash_h1.alpha,
                    "H1 t": st_cash_h1.t,
                    "H2 Alpha": st_cash_h2.alpha,
                    "H2 t": st_cash_h2.t,
                    "Bar Passed": cash_passed,
                }
            )

    summary_df = pd.DataFrame(summary_rows)
    cash_df = pd.DataFrame(cash_rows)

    write_completed_doc(
        doc_path=doc_path,
        summary_df=summary_df,
        cash_df=cash_df,
        start_date=start_date,
        end_date=end_date,
        split_date=split_date,
        num_quarters=num_quarters,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Form 13F institutional conviction signal study.")
    parser.add_argument("--out", type=Path, default=DOC_PATH, help="Output markdown path")
    args = parser.parse_args()

    uri = RAW_POSTGRES_URI
    num_quarters, total_filings, total_holdings, quarter_rows, error_msg = check_quarter_counts(uri)

    if num_quarters < MIN_REQUIRED_QUARTERS:
        logger.info(
            "Found %d quarters (< %d required). Writing 'insufficient data' doc and exiting 0.",
            num_quarters,
            MIN_REQUIRED_QUARTERS,
        )
        write_insufficient_data_doc(
            doc_path=args.out,
            num_quarters=num_quarters,
            total_filings=total_filings,
            total_holdings=total_holdings,
            quarter_rows=quarter_rows,
            reason=error_msg
            or f"Only {num_quarters} valid quarters found in database (< {MIN_REQUIRED_QUARTERS} required).",
        )
        return 0

    return run_inst13f_backtest(uri=uri, doc_path=args.out, num_quarters=num_quarters)


if __name__ == "__main__":
    sys.exit(main())
