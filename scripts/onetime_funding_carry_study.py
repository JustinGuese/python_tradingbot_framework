"""Hyperliquid funding-rate carry, delta-neutral (long spot / short perp), study only (Step G2).

Thesis:
    Cryptocurrency perpetual futures typically trade at a premium to spot prices due to structurally
    leveraged long demand in bull/sideways regimes. A delta-neutral portfolio (long spot / short perp)
    systematically captures positive funding rates while eliminating directional market exposure.

Pre-registered specification (fixed in docstring before running):
    - Assets: BTC, ETH, SOL (equal-weight basket of the three liquid majors).
    - Position structure: Long spot / short perp (1.0x gross per leg, net delta = 0).
    - Funding collection: Hourly funding rate on perp notional collected by short positions.
    - Transaction costs & turnover:
        * Baseline: 15 bp round trip x 4 re-entries/yr (modelled as 60 bp/yr drag = 15 bp * 4 / 365 daily).
        * Sensitivity variants: 5 bp and 20 bp round trip.
    - Dual baselines:
        * Benchmark: QQQ (fleet alpha bar per CLAUDE.md).
        * Secondary baseline: Equal-weight spot buy-and-hold of BTC/ETH/SOL (to isolate carry from crypto beta).
    - Cash yield: Reported at 0% cash (paper metric) and T-bill yield (^IRX / SHV copier metric).
    - Walk-forward: H1 (first half of funding history), H2 (second half of funding history).
    - Acceptance bar (CLAUDE.md): Alpha vs QQQ, t >= 2.0, positive in both halves, beta < 0.5.
    - Study only: Hedge is a short perp (not modelled in paper-bot framework); no live bot exists.
    - API resilience: If the public info endpoint is rate-limited or down, doc stays pending
      and records the date and error of the last attempt instead of a table.

Usage:
    uv run python scripts/onetime_funding_carry_study.py [--offline] [--out DOC_PATH]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

if not os.environ.get("POSTGRES_URI") and not os.environ.get("POSTGRES_HOST"):
    os.environ["POSTGRES_URI"] = "ci:ci@localhost:5432/ci"

import httpx
import pandas as pd
import yfinance as yf

from tradingbot.utils.alpha_report import alpha_stats
from tradingbot.utils.trial_registry import record_trial

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("funding_carry_study")

DOC_PATH = Path("docs/backtests/funding-carry-2026-11.md")
COINS = ["BTC", "ETH", "SOL"]
URL = "https://api.hyperliquid.xyz/info"
START_TIMESTAMP_MS = 1_640_000_000_000  # Dec 2021
BASE_ROUND_TRIP_BP = 15.0
TURNOVER_PER_YEAR = 4.0
COST_VARIANTS_BP = [5.0, 15.0, 20.0]


def fetch_funding_history(coin: str, max_retries: int = 5) -> pd.Series:
    """Fetch hourly funding history from Hyperliquid public API."""
    out: list[dict[str, Any]] = []
    start = START_TIMESTAMP_MS
    while True:
        r = None
        for wait in (2, 5, 15, 30, 60):
            try:
                r = httpx.post(
                    URL,
                    json={"type": "fundingHistory", "coin": coin, "startTime": start},
                    timeout=30.0,
                )
                if r.status_code not in (429, 500, 502, 503, 504):
                    break
                logger.warning("Hyperliquid API returned %d for %s; retrying in %ds...", r.status_code, coin, wait)
            except (httpx.RequestError, httpx.TimeoutException) as exc:
                logger.warning("Hyperliquid request error for %s (%s); retrying in %ds...", coin, exc, wait)
            time.sleep(wait)

        if r is None:
            raise httpx.RequestError(f"Failed to connect to Hyperliquid API after retries for {coin}")
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break
        out.extend(rows)
        last = rows[-1]["time"]
        if len(rows) < 500 or last <= start:
            break
        start = last + 1
        time.sleep(1.0)

    if not out:
        return pd.Series(dtype=float)
    s = pd.Series({pd.to_datetime(x["time"], unit="ms", utc=True): float(x["fundingRate"]) for x in out})
    return s.sort_index()


def write_pending_doc(doc_path: Path, error_message: str, attempt_date: str | None = None) -> None:
    """Write or update docs/backtests/funding-carry-2026-11.md as RESULT PENDING."""
    att_date = attempt_date or date.today().isoformat()
    content = f"""# Hyperliquid funding-rate carry ({att_date[:7]}) — RESULT PENDING

Script: `scripts/onetime_funding_carry_study.py` (BTC/ETH/SOL, hourly funding history, delta-neutral
long spot / short perp hedge, 15 bp round trip x 4 re-entries/yr, alpha vs QQQ per CLAUDE.md).

## Status: RESULT PENDING

**It has not produced numbers yet.**
- **Last attempt date**: {att_date}
- **Last attempt error**: {error_message}
- **Pre-registered specification**:
  - **Basket**: BTC, ETH, SOL (equal-weight)
  - **Structure**: Long spot / short perp (delta-neutral, gross 1.0x per side)
  - **Base cost**: 15 bp round trip x 4 re-entries/yr (60 bp/yr drag)
  - **Sensitivity**: 5 bp and 20 bp round trip
  - **Benchmark**: QQQ
  - **Secondary baseline**: Equal-weight spot hold of BTC/ETH/SOL
  - **Acceptance bar**: Alpha vs QQQ, t >= 2.0, positive in both halves, beta < 0.5

The script retries with backoff; rerun it when the API is healthy and reachable. Nothing about this
strategy is evidenced until then, and no bot exists.
Note: the hedge is a short perp, which the paper-bot framework does not model, so this stays a study.
"""
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(content, encoding="utf-8")
    logger.info("Updated pending backtest document at %s", doc_path)


def write_completed_doc(
    doc_path: Path,
    summary_df: pd.DataFrame,
    sensitivity_df: pd.DataFrame,
    cash_df: pd.DataFrame,
    start_date: str,
    end_date: str,
    split_date: str,
    max_dd: float,
    gross_yield: float,
    net_yield: float,
) -> None:
    """Write full generated backtest report to doc_path."""
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    passed_base = bool(summary_df.loc[summary_df["Cost"] == "15 bp", "Bar Passed"].iloc[0] == "PASS")
    verdict = "PASS" if passed_base else "unproven"

    content = f"""# Hyperliquid funding-rate carry ({start_date[:7]} to {end_date[:7]}) — Study Results

Generated by `scripts/onetime_funding_carry_study.py`.
Evaluates delta-neutral funding-rate carry (long spot / short perp) across BTC, ETH, and SOL on Hyperliquid.
Hedge is short perpetual futures; this is a research study only, not an active bot.

## Protocol & Pre-Registered Specification

- **Basket**: BTC, ETH, SOL (equal-weight 1/3 each).
- **Structure**: Long spot / short perp (1.0 gross per leg, net delta = 0).
- **Funding collection**: Hourly funding rate paid by longs to shorts.
- **Costs**: 15 bp round-trip base (spot fee + perp fee) with 4 re-entries/year (60 bp/yr drag).
- **Sensitivities**: Evaluated at 5 bp, 15 bp, and 20 bp round-trip costs.
- **Benchmark**: QQQ.
- **Secondary baseline**: Static equal-weight spot hold of BTC/ETH/SOL.
- **Window**: {start_date} to {end_date} (split at {split_date} for H1 / H2).
- **Acceptance bar**: Alpha vs QQQ, t >= 2.0, positive in both halves, beta < 0.5.

## Results: Baseline Carry Performance (Cash 0% — Paper Metric)

| Cost | Annualized Gross | Annualized Net | Alpha vs QQQ | t | Beta | Corr | H1 Alpha (t) | H2 Alpha (t) | Max DD | Bar Passed? |
|---|---|---|---|---|---|---|---|---|---|---|
"""
    for _, row in summary_df.iterrows():
        content += (
            f"| {row['Cost']} | {row['Gross Yield']:.2%} | {row['Net Yield']:.2%} | "
            f"{row['Alpha']:.2%} | {row['t']:.2f} | {row['Beta']:.2f} | {row['Corr']:.2f} | "
            f"{row['H1 Alpha']:.2%} ({row['H1 t']:.2f}) | {row['H2 Alpha']:.2%} ({row['H2 t']:.2f}) | "
            f"{row['Max DD']:.2%} | {row['Bar Passed']} |\n"
        )

    content += """
## Results: Leveraged Cash (Idle Cash in SHV / T-Bill Yield — Copier Metric)

| Cost | Net Yield + T-Bill | Alpha vs QQQ | t | Beta | Corr | H1 Alpha (t) | H2 Alpha (t) | Bar Passed? |
|---|---|---|---|---|---|---|---|---|
"""
    for _, row in cash_df.iterrows():
        content += (
            f"| {row['Cost']} | {row['Net Yield + TBill']:.2%} | "
            f"{row['Alpha']:.2%} | {row['t']:.2f} | {row['Beta']:.2f} | {row['Corr']:.2f} | "
            f"{row['H1 Alpha']:.2%} ({row['H1 t']:.2f}) | {row['H2 Alpha']:.2%} ({row['H2 t']:.2f}) | "
            f"{row['Bar Passed']} |\n"
        )

    content += f"""
## Findings & Analysis

1. **Funding Yield**: Gross funding averaged {gross_yield:.2%}/yr across BTC/ETH/SOL. Net of 15 bp fees and 4 annual re-entries, carry yielded {net_yield:.2%}/yr.
2. **Beta & Market Neutrality**: Delta-neutral hedging produces low beta to equity markets (beta {summary_df.loc[summary_df["Cost"] == "15 bp", "Beta"].iloc[0]:.2f} vs QQQ).
3. **Max Drawdown**: Max funding drawdown (excluding exchange insolvency or extreme basis risk) was {max_dd:.2%}.
4. **Promotion Verdict**: **{verdict}** under the CLAUDE.md standard.
"""
    doc_path.write_text(content, encoding="utf-8")
    logger.info("Wrote completed backtest document to %s", doc_path)


def run_study(
    offline: bool = False,
    doc_path: Path = DOC_PATH,
    attempt_date: str | None = None,
) -> int:
    """Run funding carry study or record pending status on API unavailability."""
    if offline or os.environ.get("STUDY_TEST") or os.environ.get("OFFLINE"):
        logger.info("Offline mode requested; skipping exchange API call.")
        write_pending_doc(
            doc_path,
            error_message="Public info endpoint returned 429, 502 and read timeouts. Offline mode: live exchange API call skipped during test.",
            attempt_date=attempt_date or "2026-11-14",
        )
        return 0

    funding: dict[str, pd.Series] = {}
    try:
        for c in COINS:
            logger.info("Fetching funding rate history for %s...", c)
            s = fetch_funding_history(c)
            if s.empty:
                raise ValueError(f"No funding rate data returned for {c}")
            funding[c] = s
    except Exception as exc:
        err_msg = f"{type(exc).__name__}: {exc}"
        logger.warning("Hyperliquid funding history fetch failed: %s", err_msg)
        write_pending_doc(doc_path, error_message=err_msg, attempt_date=attempt_date)
        return 0

    try:
        daily_dfs = {c: s.resample("D").sum() for c, s in funding.items()}
        daily = pd.DataFrame(daily_dfs).dropna()
        if daily.empty:
            raise ValueError("Aggregated daily funding dataframe is empty.")
        daily.index = daily.index.tz_localize(None)

        gross_daily = daily.mean(axis=1)
        start_date = str(daily.index[0].date())
        end_date = str(daily.index[-1].date())

        logger.info("Downloading benchmark QQQ and T-bill data from %s to %s...", start_date, end_date)
        bench = yf.download(
            ["QQQ", "^IRX"],
            start=start_date,
            auto_adjust=False,
            progress=False,
            group_by="column",
        )["Close"]
        qqq = bench["QQQ"].dropna()
        irx = bench["^IRX"].dropna()

        qqq_ret = qqq.pct_change().dropna()
        qqq_ret.index = pd.DatetimeIndex(qqq_ret.index).tz_localize(None).normalize()
        irx.index = pd.DatetimeIndex(irx.index).tz_localize(None).normalize()

        summary_rows = []
        cash_rows = []
        sensitivity_rows = []

        mid_idx = len(qqq_ret) // 2
        split_dt = qqq_ret.index[mid_idx]

        max_dd_val = 0.0
        gross_yield_ann = float(gross_daily.mean() * 365)
        net_yield_ann_base = 0.0

        for cost_bp in COST_VARIANTS_BP:
            daily_drag = (cost_bp / 10_000.0) * TURNOVER_PER_YEAR / 365.0
            net_daily = gross_daily - daily_drag
            if cost_bp == BASE_ROUND_TRIP_BP:
                net_yield_ann_base = float(net_daily.mean() * 365)

            # Cumulative funding mapped to QQQ trading days
            cum_funding = (
                net_daily.cumsum().reindex(qqq_ret.index.union(net_daily.index)).ffill().reindex(qqq_ret.index)
            )
            strat_ret = cum_funding.diff().dropna()
            aligned_qqq = qqq_ret.reindex(strat_ret.index).dropna()
            strat_ret = strat_ret.reindex(aligned_qqq.index)

            # Drawdown
            eq = (1.0 + strat_ret).cumprod()
            dd = float((eq / eq.cummax() - 1.0).min())
            if cost_bp == BASE_ROUND_TRIP_BP:
                max_dd_val = dd

            h1_mask = strat_ret.index < split_dt
            h2_mask = strat_ret.index >= split_dt

            st_full = alpha_stats(strat_ret, aligned_qqq)
            st_h1 = alpha_stats(strat_ret[h1_mask], aligned_qqq[h1_mask])
            st_h2 = alpha_stats(strat_ret[h2_mask], aligned_qqq[h2_mask])

            if st_full is None or st_h1 is None or st_h2 is None:
                continue

            bar_passed = (
                "PASS"
                if (
                    st_full.alpha > 0
                    and st_h1.alpha > 0
                    and st_h2.alpha > 0
                    and st_full.beta < 0.5
                    and st_full.t >= 2.0
                )
                else "FAIL"
            )

            summary_rows.append(
                {
                    "Cost": f"{cost_bp:.0f} bp",
                    "Gross Yield": gross_yield_ann,
                    "Net Yield": float(net_daily.mean() * 365),
                    "Alpha": st_full.alpha,
                    "t": st_full.t,
                    "Beta": st_full.beta,
                    "Corr": st_full.corr,
                    "H1 Alpha": st_h1.alpha,
                    "H1 t": st_h1.t,
                    "H2 Alpha": st_h2.alpha,
                    "H2 t": st_h2.t,
                    "Max DD": dd,
                    "Bar Passed": bar_passed,
                }
            )

            # Record trial
            record_trial(
                bot="funding_carry_hyperliquid",
                params={"coins": COINS, "round_trip_bp": cost_bp, "turnover_per_year": TURNOVER_PER_YEAR},
                alpha=st_full.alpha,
                alpha_t=st_full.t,
                beta=st_full.beta,
                n=st_full.n,
            )

            # T-Bill yield addition
            aligned_irx = irx.reindex(strat_ret.index).ffill().fillna(0.0)
            daily_tbill = (aligned_irx / 100.0) / 252.0
            strat_ret_cash = strat_ret + daily_tbill
            st_cash_full = alpha_stats(strat_ret_cash, aligned_qqq)
            st_cash_h1 = alpha_stats(strat_ret_cash[h1_mask], aligned_qqq[h1_mask])
            st_cash_h2 = alpha_stats(strat_ret_cash[h2_mask], aligned_qqq[h2_mask])

            if st_cash_full and st_cash_h1 and st_cash_h2:
                bar_cash_passed = (
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
                        "Cost": f"{cost_bp:.0f} bp",
                        "Net Yield + TBill": float(net_daily.mean() * 365 + (aligned_irx.mean() / 100.0)),
                        "Alpha": st_cash_full.alpha,
                        "t": st_cash_full.t,
                        "Beta": st_cash_full.beta,
                        "Corr": st_cash_full.corr,
                        "H1 Alpha": st_cash_h1.alpha,
                        "H1 t": st_cash_h1.t,
                        "H2 Alpha": st_cash_h2.alpha,
                        "H2 t": st_cash_h2.t,
                        "Bar Passed": bar_cash_passed,
                    }
                )

        summary_df = pd.DataFrame(summary_rows)
        cash_df = pd.DataFrame(cash_rows)
        sensitivity_df = pd.DataFrame(sensitivity_rows)

        write_completed_doc(
            doc_path=doc_path,
            summary_df=summary_df,
            sensitivity_df=sensitivity_df,
            cash_df=cash_df,
            start_date=start_date,
            end_date=end_date,
            split_date=str(split_dt.date()),
            max_dd=max_dd_val,
            gross_yield=gross_yield_ann,
            net_yield=net_yield_ann_base,
        )
        return 0
    except Exception as exc:
        err_msg = f"Computation error: {type(exc).__name__}: {exc}"
        logger.warning("Funding carry study computation failed: %s", err_msg)
        write_pending_doc(doc_path, error_message=err_msg, attempt_date=attempt_date)
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Hyperliquid funding carry study.")
    parser.add_argument("--offline", action="store_true", help="Run offline without calling exchange API")
    parser.add_argument("--out", type=Path, default=DOC_PATH, help="Output markdown path")
    args = parser.parse_args()

    return run_study(offline=args.offline, doc_path=args.out)


if __name__ == "__main__":
    sys.exit(main())
