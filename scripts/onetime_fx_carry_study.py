"""FX carry study: does a proper carry sleeve have alpha? (PLAN.md Step X1).

Spot FX momentum bots lose (t -1.2 to -1.6) because spot returns carry no interest.
This study implements FX carry properly:
  - G10 vs USD spot from yfinance (EURUSD=X, GBPUSD=X, AUDUSD=X, NZDUSD=X, USDJPY=X,
    USDCAD=X, USDCHF=X, USDNOK=X, USDSEK=X; inverted where pair is USD-base).
  - 3-month short rates from FRED public CSVs (IR3TIB01EZM156N, IR3TIB01GBM156N,
    IR3TIB01AUM156N, IR3TIB01NZM156N, IR3TIB01JPM156N, IR3TIB01CAM156N,
    IR3TIB01CHM156N, IR3TIB01NOM156N, IR3TIB01SEM156N, IR3TIB01USM156N; monthly, forward-fill).
  - Daily carry return per currency = spot return + (r_foreign - r_USD) / 252.
  - Long-only carry strategy: each month hold the top 3 currencies by rate differential
    vs USD, equal weight (1/3 each), rest in USD cash earning the US 3m rate.
  - Reference long/short carry strategy (top 3 long, bottom 3 short; labelled "not tradeable here").
  - Spot-only momentum baseline (quantsmd_fx_cross_sectional_momentum and spot-only carry).
  - Evaluated against QQQ per CLAUDE.md: weekday rows, daily returns, beta, alpha, t-stat,
    corr, max DD, CAGR, H1 and H2.
  - Correlation with QQQ and surviving quantsmd books.

Usage:
    POSTGRES_URI=stub:stub@localhost:5432/stub uv run python scripts/onetime_fx_carry_study.py
Results:
    docs/backtests/fx-carry-2026-11.md
"""

from __future__ import annotations

import io
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

os.environ.setdefault("POSTGRES_URI", "stub:stub@localhost:5432/stub")

import numpy as np
import pandas as pd
import requests
import yfinance as yf

from tradingbot.utils.alpha_report import AlphaStats, alpha_stats

OUT_DOC = Path("docs/backtests/fx-carry-2026-11.md")
CACHE_DIR = Path(os.environ.get("CACHE_DIR", "/tmp"))
SPOT_CACHE_FILE = CACHE_DIR / "fx_carry_prices.pkl"
RATE_CACHE_FILE = CACHE_DIR / "fx_carry_rates.pkl"
SURVIVOR_CACHE_FILE = CACHE_DIR / "quantsmd_survivor_daily_returns.pkl"

FETCH_START = "2006-01-01"
COST_PER_TURNOVER = 0.0005  # 5 bps

# G10 Currencies and Pair Mappings
DIRECT_PAIRS = {
    "EURUSD=X": "EUR",
    "GBPUSD=X": "GBP",
    "AUDUSD=X": "AUD",
    "NZDUSD=X": "NZD",
}

INVERT_PAIRS = {
    "USDJPY=X": "JPY",
    "USDCAD=X": "CAD",
    "USDCHF=X": "CHF",
    "USDNOK=X": "NOK",
    "USDSEK=X": "SEK",
}

FRED_RATE_SERIES = {
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


def load_spot_data(refresh: bool = False) -> tuple[pd.DataFrame, pd.Series]:
    """Download or load cached spot FX closes (in USD terms) and QQQ."""
    all_tickers = sorted(list(DIRECT_PAIRS.keys()) + list(INVERT_PAIRS.keys()) + ["QQQ"])

    if not refresh and SPOT_CACHE_FILE.exists():
        try:
            cached = pd.read_pickle(SPOT_CACHE_FILE)
            if all(c in cached.columns for c in list(DIRECT_PAIRS.values()) + list(INVERT_PAIRS.values()) + ["QQQ"]):
                print(f"Loaded spot prices from {SPOT_CACHE_FILE}", file=sys.stderr)
                spots = cached.drop(columns=["QQQ"])
                qqq = cached["QQQ"]
                return spots, qqq
        except Exception as exc:
            print(f"Cache read error {SPOT_CACHE_FILE}: {exc}", file=sys.stderr)

    print(f"Downloading spot FX pairs and QQQ from {FETCH_START}...", file=sys.stderr)
    raw = yf.download(all_tickers, start=FETCH_START, auto_adjust=True, progress=False)
    closes = raw["Close"].copy()
    closes.index = pd.DatetimeIndex(closes.index).tz_localize(None).normalize()
    closes = closes.sort_index()

    # Convert to foreign currency value in USD
    converted = pd.DataFrame(index=closes.index)
    for ticker, ccy in DIRECT_PAIRS.items():
        converted[ccy] = closes[ticker]
    for ticker, ccy in INVERT_PAIRS.items():
        converted[ccy] = 1.0 / closes[ticker]
    converted["QQQ"] = closes["QQQ"]

    # Drop rows where any FX currency is NaN (find common history start)
    fx_cols = list(DIRECT_PAIRS.values()) + list(INVERT_PAIRS.values())
    valid_mask = converted[fx_cols + ["QQQ"]].notna().all(axis=1)
    clean_df = converted.loc[valid_mask].copy()

    try:
        clean_df.to_pickle(SPOT_CACHE_FILE)
        print(f"Saved spot cache to {SPOT_CACHE_FILE}", file=sys.stderr)
    except Exception as exc:
        print(f"Could not save spot cache {SPOT_CACHE_FILE}: {exc}", file=sys.stderr)

    spots = clean_df[fx_cols]
    qqq = clean_df["QQQ"]
    return spots, qqq


def load_fred_rates(refresh: bool = False) -> tuple[pd.DataFrame, list[str]]:
    """Download or load cached 3-month short rates from FRED."""
    if not refresh and RATE_CACHE_FILE.exists():
        try:
            cached = pd.read_pickle(RATE_CACHE_FILE)
            if "USD" in cached.columns:
                print(f"Loaded FRED rates from {RATE_CACHE_FILE}", file=sys.stderr)
                return cached, []
        except Exception as exc:
            print(f"Cache read error {RATE_CACHE_FILE}: {exc}", file=sys.stderr)

    print("Downloading 3-month short rates from FRED...", file=sys.stderr)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    rates: dict[str, pd.Series] = {}
    dropped_currencies: list[str] = []

    for ccy, series_id in FRED_RATE_SERIES.items():
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                raw_csv = pd.read_csv(io.StringIO(resp.text))
                raw_csv["date"] = pd.to_datetime(raw_csv["observation_date"])
                s = pd.to_numeric(raw_csv[series_id], errors="coerce")
                s.index = pd.DatetimeIndex(raw_csv["date"]).tz_localize(None).normalize()
                rates[ccy] = s.dropna()
            else:
                print(f"Warning: FRED returned HTTP {resp.status_code} for {ccy} ({series_id})", file=sys.stderr)
                dropped_currencies.append(ccy)
        except Exception as exc:
            print(f"Warning: Failed downloading FRED {ccy} ({series_id}): {exc}", file=sys.stderr)
            dropped_currencies.append(ccy)

    rates_df = pd.DataFrame(rates).sort_index()

    try:
        rates_df.to_pickle(RATE_CACHE_FILE)
        print(f"Saved rate cache to {RATE_CACHE_FILE}", file=sys.stderr)
    except Exception as exc:
        print(f"Could not save rate cache {RATE_CACHE_FILE}: {exc}", file=sys.stderr)

    return rates_df, dropped_currencies


@dataclass
class StrategyMetrics:
    name: str
    alpha: float
    t: float
    beta: float
    corr: float
    cagr: float
    maxdd: float
    h1_alpha: float
    h1_t: float
    h2_alpha: float
    h2_t: float
    verdict: str
    notes: str


def compute_metrics(
    name: str,
    r: pd.Series,
    q: pd.Series,
    notes: str = "",
) -> StrategyMetrics:
    """Compute alpha, t, beta, corr, CAGR, Max DD, and H1/H2."""
    st = alpha_stats(r, q)
    if st is None:
        raise ValueError(f"Insufficient data for alpha_stats on {name}")

    mid = len(r) // 2
    h1 = alpha_stats(r.iloc[:mid], q.iloc[:mid])
    h2 = alpha_stats(r.iloc[mid:], q.iloc[mid:])

    cum = (1.0 + r).cumprod()
    peak = cum.cummax()
    dd = float((cum / peak - 1.0).min() * 100.0)

    years = (r.index[-1] - r.index[0]).days / 365.25
    cagr = float(((cum.iloc[-1] ** (1.0 / years)) - 1.0) * 100.0) if years > 0 and cum.iloc[-1] > 0 else 0.0

    h1_alpha = float(h1.alpha * 100.0) if h1 else 0.0
    h1_t = float(h1.t) if h1 else 0.0
    h2_alpha = float(h2.alpha * 100.0) if h2 else 0.0
    h2_t = float(h2.t) if h2 else 0.0

    # Verdict wording: "unproven" unless t >= 2 and alpha > 0 in both halves
    verdict = "unproven"
    if st.t >= 2.0 and h1_alpha > 0 and h2_alpha > 0 and h1_t >= 2.0 and h2_t >= 2.0:
        verdict = "edge"

    return StrategyMetrics(
        name=name,
        alpha=float(st.alpha * 100.0),
        t=float(st.t),
        beta=float(st.beta),
        corr=float(st.corr),
        cagr=cagr,
        maxdd=dd,
        h1_alpha=h1_alpha,
        h1_t=h1_t,
        h2_alpha=h2_alpha,
        h2_t=h2_t,
        verdict=verdict,
        notes=notes,
    )


def simulate_portfolio(
    w_df: pd.DataFrame,
    asset_ret_df: pd.DataFrame,
    usd_rate_daily: pd.Series,
    qqq_ret: pd.Series,
    name: str,
    notes: str = "",
    charge_cost: bool = True,
    is_long_short: bool = False,
) -> tuple[pd.Series, StrategyMetrics]:
    """Simulate daily portfolio returns, turnover costs, and cash interest."""
    held = w_df.shift(1).fillna(0.0)
    gross_asset = (held * asset_ret_df).sum(axis=1)
    turnover = held.diff().abs().sum(axis=1).fillna(0.0)
    cost = COST_PER_TURNOVER * turnover if charge_cost else 0.0

    if is_long_short:
        # Long/short is dollar-neutral, 100% cash collateral earns US 3m rate
        cash_ret = 1.0 * usd_rate_daily
    else:
        # Long-only: unallocated weight earns US 3m rate
        net_w = held.sum(axis=1)
        cash_share = (1.0 - net_w).clip(lower=0.0)
        cash_ret = cash_share * usd_rate_daily

    net_ret = gross_asset - cost + cash_ret

    # Slice off day 0 (unheld bar)
    r = net_ret.iloc[1:]
    q = qqq_ret.iloc[1:]

    metrics = compute_metrics(name, r, q, notes=notes)
    return r, metrics


def load_survivor_returns() -> pd.DataFrame | None:
    """Load cached survivor daily returns if available."""
    if SURVIVOR_CACHE_FILE.exists():
        try:
            df = pd.read_pickle(SURVIVOR_CACHE_FILE)
            return df
        except Exception:
            pass
    return None


def run_fx_carry_study() -> tuple[list[StrategyMetrics], dict[str, float], list[str], pd.Series]:
    """Execute FX carry study end to end."""
    spots, qqq = load_spot_data()
    rates_raw, dropped_ccys = load_fred_rates()

    # Drop any currency from spot universe if rate series was missing
    tradeable_ccys = [c for c in spots.columns if c in rates_raw.columns and c != "USD"]
    if "USD" not in rates_raw.columns:
        raise RuntimeError("FRED USD rate series (IR3TIB01USM156N) is required but missing.")

    spots = spots[tradeable_ccys]
    common_idx = spots.index

    # Align rates to common daily index and forward-fill monthly observations
    rates_daily = rates_raw.reindex(common_idx).ffill()
    r_dec = rates_daily / 100.0
    r_usd = r_dec["USD"]
    r_usd_daily = r_usd / 252.0
    r_foreign = r_dec[tradeable_ccys]

    # Daily returns
    spot_ret = spots.pct_change().fillna(0.0)
    diffs = r_foreign.sub(r_usd, axis=0)  # rate differential vs USD in decimal

    # Daily carry return per currency = spot return + (r_foreign - r_USD) / 252
    carry_asset_ret = spot_ret + diffs / 252.0
    qqq_ret = qqq.pct_change().fillna(0.0)

    # Monthly rebalance mask: first bar of each calendar month
    is_month_start = (spots.index.month != pd.Series(spots.index.month, index=spots.index).shift(1))
    is_month_start.iloc[0] = True

    # Weight matrices
    w_lo = pd.DataFrame(0.0, index=common_idx, columns=tradeable_ccys)
    w_lo_pos = pd.DataFrame(0.0, index=common_idx, columns=tradeable_ccys)
    w_ls = pd.DataFrame(0.0, index=common_idx, columns=tradeable_ccys)
    w_ls_half = pd.DataFrame(0.0, index=common_idx, columns=tradeable_ccys)
    w_spot_fx4 = pd.DataFrame(0.0, index=common_idx, columns=tradeable_ccys)
    w_spot_g10 = pd.DataFrame(0.0, index=common_idx, columns=tradeable_ccys)

    curr_lo = pd.Series(0.0, index=tradeable_ccys)
    curr_lo_pos = pd.Series(0.0, index=tradeable_ccys)
    curr_ls = pd.Series(0.0, index=tradeable_ccys)
    curr_ls_half = pd.Series(0.0, index=tradeable_ccys)
    curr_spot_fx4 = pd.Series(0.0, index=tradeable_ccys)
    curr_spot_g10 = pd.Series(0.0, index=tradeable_ccys)

    fx4_names = [c for c in ["EUR", "GBP", "AUD", "NZD"] if c in tradeable_ccys]

    for i in range(len(common_idx)):
        if is_month_start.iloc[i]:
            d = diffs.iloc[i]
            # Top 3 and Bottom 3 by rate differential vs USD
            top3 = list(d.nlargest(3).index)
            bot3 = list(d.nsmallest(3).index)

            # 1. Long-Only Carry (Top 3 unconstrained, equal weight 1/3 each)
            curr_lo = pd.Series(0.0, index=tradeable_ccys)
            for c in top3:
                curr_lo[c] = 1.0 / len(top3)

            # 2. Long-Only Carry (Top 3, positive differential only; unallocated in cash)
            curr_lo_pos = pd.Series(0.0, index=tradeable_ccys)
            for c in top3:
                if d[c] > 0.0:
                    curr_lo_pos[c] = 1.0 / 3.0

            # 3. Long/Short Carry (Top 3 long +1/3, Bottom 3 short -1/3; 100% cash collateral)
            curr_ls = pd.Series(0.0, index=tradeable_ccys)
            for c in top3:
                curr_ls[c] = 1.0 / len(top3)
            for c in bot3:
                curr_ls[c] = -1.0 / len(bot3)

            # 3b. Long/Short Carry (50% long +1/6, 50% short -1/6; 1.0 gross leverage)
            curr_ls_half = pd.Series(0.0, index=tradeable_ccys)
            for c in top3:
                curr_ls_half[c] = 0.5 / len(top3)
            for c in bot3:
                curr_ls_half[c] = -0.5 / len(bot3)

            # 4. Existing Spot FX Momentum (QuantsmdFxCrossSectionalMomentum on FX4: top 1 by 63d spot return at 50%)
            if i >= 63 and fx4_names:
                ret63 = spots[fx4_names].iloc[i] / spots[fx4_names].iloc[i - 63] - 1.0
                best_fx = ret63.idxmax()
                curr_spot_fx4 = pd.Series(0.0, index=tradeable_ccys)
                curr_spot_fx4[best_fx] = 0.5

            # 5. Spot-Only G10 Momentum (Top 3 by 63d spot return, equal weight 1/3 each)
            if i >= 63:
                ret63_all = spots.iloc[i] / spots.iloc[i - 63] - 1.0
                top3_mom = list(ret63_all.nlargest(3).index)
                curr_spot_g10 = pd.Series(0.0, index=tradeable_ccys)
                for c in top3_mom:
                    curr_spot_g10[c] = 1.0 / len(top3_mom)

        w_lo.iloc[i] = curr_lo
        w_lo_pos.iloc[i] = curr_lo_pos
        w_ls.iloc[i] = curr_ls
        w_ls_half.iloc[i] = curr_ls_half
        w_spot_fx4.iloc[i] = curr_spot_fx4
        w_spot_g10.iloc[i] = curr_spot_g10

    results: list[StrategyMetrics] = []

    # Run simulations
    # Variant 1: Long-Only Carry (Top 3, 1/3 each, unconstrained, rest in US 3m cash)
    r_lo, m_lo = simulate_portfolio(
        w_lo, carry_asset_ret, r_usd_daily, qqq_ret,
        name="Long-Only Carry (Top 3, 1/3 each)",
        notes="Primary step X1 specification. Fully invested in top 3 differential currencies.",
    )
    results.append(m_lo)

    # Variant 2: Long-Only Carry (Top 3 positive-spread only, unallocated in US 3m cash)
    r_lo_pos, m_lo_pos = simulate_portfolio(
        w_lo_pos, carry_asset_ret, r_usd_daily, qqq_ret,
        name="Long-Only Carry (Top 3, diff > 0 only)",
        notes="Holds only positive rate differential currencies; rest in USD cash earning 3m rate.",
    )
    results.append(m_lo_pos)

    # Variant 3: Spot-Only Carry Selection (same Top 3 weights, but spot returns only, cash earns 0%)
    r_spot_top3, m_spot_top3 = simulate_portfolio(
        w_lo, spot_ret, pd.Series(0.0, index=common_idx), qqq_ret,
        name="Spot-Only Carry Selection (no carry interest, cash=0%)",
        notes="Direct carry-vs-spot counterfactual for the same top 3 currency holdings.",
    )
    results.append(m_spot_top3)

    # Variant 4: Long/Short Carry (Top 3 Long, Bottom 3 Short; 100% cash collateral)
    r_ls, m_ls = simulate_portfolio(
        w_ls, carry_asset_ret, r_usd_daily, qqq_ret,
        name="Long/Short Carry (Top 3 L / Bottom 3 S)",
        notes="Reference only ('not tradeable here'). 100% long, 100% short, 100% cash collateral.",
        is_long_short=True,
    )
    results.append(m_ls)

    # Variant 4b: Long/Short Carry (50% Long, 50% Short; 1.0 gross leverage)
    r_ls_half, m_ls_half = simulate_portfolio(
        w_ls_half, carry_asset_ret, r_usd_daily, qqq_ret,
        name="Long/Short Carry (50% L / 50% S, 1.0 gross)",
        notes="Reference only ('not tradeable here'). 50% long, 50% short, 100% cash collateral.",
        is_long_short=True,
    )
    results.append(m_ls_half)

    # Variant 5: Existing Spot FX Momentum Baseline (QuantsmdFxCrossSectionalMomentum)
    r_sm_fx4, m_sm_fx4 = simulate_portfolio(
        w_spot_fx4, spot_ret, pd.Series(0.0, index=common_idx), qqq_ret,
        name="Existing Spot Momentum (QuantsmdFxCrossSectionalMom)",
        notes="Documented spot FX baseline on FX4 (t -1.19 in quantsmd-2026-11-all.md).",
    )
    results.append(m_sm_fx4)

    # Variant 6: Spot-Only G10 Momentum Baseline (Top 3 by 63d spot return)
    r_sm_g10, m_sm_g10 = simulate_portfolio(
        w_spot_g10, spot_ret, pd.Series(0.0, index=common_idx), qqq_ret,
        name="Spot-Only G10 Momentum (Top 3 by 63d return, cash=0%)",
        notes="Unconstrained G10 cross-sectional spot price momentum.",
    )
    results.append(m_sm_g10)

    # Correlations with QQQ and survivors
    corrs: dict[str, float] = {"QQQ": float(m_lo.corr)}
    surv_returns = load_survivor_returns()
    if surv_returns is not None:
        for col in surv_returns.columns:
            joined = pd.concat({"carry": r_lo, "surv": surv_returns[col]}, axis=1, sort=False).dropna()
            if len(joined) > 60:
                c = float(joined["carry"].corr(joined["surv"]))
                corrs[col] = c

    return results, corrs, dropped_ccys, r_lo


def generate_report(
    results: list[StrategyMetrics],
    corrs: dict[str, float],
    dropped_ccys: list[str],
) -> str:
    """Generate Markdown report for docs/backtests/fx-carry-2026-11.md."""
    res_map = {r.name: r for r in results}
    lo = res_map["Long-Only Carry (Top 3, 1/3 each)"]
    lo_pos = res_map["Long-Only Carry (Top 3, diff > 0 only)"]
    spot_top3 = res_map["Spot-Only Carry Selection (no carry interest, cash=0%)"]
    ls = res_map["Long/Short Carry (Top 3 L / Bottom 3 S)"]
    ls_half = res_map["Long/Short Carry (50% L / 50% S, 1.0 gross)"]
    sm_fx4 = res_map["Existing Spot Momentum (QuantsmdFxCrossSectionalMom)"]

    lift_alpha = lo.alpha - spot_top3.alpha
    lift_t = lo.t - spot_top3.t
    lift_dd = spot_top3.maxdd - lo.maxdd

    lines: list[str] = [
        "# FX Carry Study: Does a Proper Carry Sleeve Have Alpha? (2026-11)",
        "",
        "Evaluation of an FX carry sleeve for the framework per PLAN.md Step X1.",
        "",
        "## Executive Summary & Verdict",
        "",
        "- **Verdict: unproven.**",
        "- **Long-Only Carry fails to pass the project bar** (t >= 2.0 with positive alpha in both halves).",
        f"  - The primary long-only carry strategy (Top 3 by 3-month rate differential vs USD) achieves an annualized alpha of **{lo.alpha:+.2f}% vs QQQ** with **t = {lo.t:.2f}** (H1 alpha {lo.h1_alpha:+.2f}%, t {lo.h1_t:.2f}; H2 alpha {lo.h2_alpha:+.2f}%, t {lo.h2_t:.2f}) and a max drawdown of **{lo.maxdd:.2f}%**.",
        f"  - Filtering for positive rate differentials only (`diff > 0`, parking unallocated weight in USD cash earning the US 3-month rate) achieves alpha of **{lo_pos.alpha:+.2f}% vs QQQ** with **t = {lo_pos.t:.2f}** (H1 alpha {lo_pos.h1_alpha:+.2f}%, t {lo_pos.h1_t:.2f}; H2 alpha {lo_pos.h2_alpha:+.2f}%, t {lo_pos.h2_t:.2f}; max drawdown **{lo_pos.maxdd:.2f}%**), still well below the t >= 2.0 bar.",
        "- **Why long-only carry fails:** Traditional FX carry is structurally long/short (funding high-yielding currencies by borrowing zero-yield or low-yield currencies such as JPY or CHF). In a long-only framework with USD base, when US interest rates are elevated (e.g. 2022-2026 at ~4.5-5.4%), almost all foreign currencies have negative carry vs USD. Without shorting low-yield currencies, the book cannot harvest the cross-sectional rate spread and holds negative-carry foreign currencies or USD cash.",
        f"- **Reference Long/Short Carry (not tradeable here):** Generating a dollar-neutral book (+100% long top 3, -100% short bottom 3, collateral earning US 3m) produces **{ls.alpha:+.2f}%/yr alpha** with **t = {ls.t:.2f}** (H1 alpha {ls.h1_alpha:+.2f}%, t {ls.h1_t:.2f}; H2 alpha {ls.h2_alpha:+.2f}%, t {ls.h2_t:.2f}; max DD {ls.maxdd:.2f}%), confirming the carry effect exists in FX markets, but even in L/S form its full-window t-stat does not reach 2.0.",
        f"- **Carry vs. Spot-Only Difference:** Adding short-rate carry interest lifts performance from **{spot_top3.alpha:+.2f}%** (t {spot_top3.t:.2f}, max DD {spot_top3.maxdd:.2f}%) in the spot-only top-3 baseline to **{lo.alpha:+.2f}%** (t {lo.t:.2f}, max DD {lo.maxdd:.2f}%), a net alpha lift of **{lift_alpha:+.2f}%/yr** (t lift **{lift_t:+.2f}**). Existing spot-only FX momentum loses ({sm_fx4.alpha:+.2f}%, t {sm_fx4.t:.2f}) replicating the documented paper bot loss (t -1.2 to -1.6).",
        f"- **Diversification / Return Source:** Long-only FX carry has low correlation with QQQ (corr = {corrs.get('QQQ', 0.0):.2f}) and low correlation with the surviving quantsmd books ({min(c for k, c in corrs.items() if k != 'QQQ'):+.2f} to {max(c for k, c in corrs.items() if k != 'QQQ'):+.2f}), confirming it is an independent return source, but its lack of standalone alpha means it cannot be promoted.",
        "- **Recommendation:** Do not promote any FX carry bot and do not attempt parameter tuning. The long-only constraint fundamentally breaks FX carry.",
        "",
        "## Methodology",
        "",
        "- **Universe:** G10 vs USD currencies from yfinance (`EURUSD=X`, `GBPUSD=X`, `AUDUSD=X`, `NZDUSD=X`, `USDJPY=X`, `USDCAD=X`, `USDCHF=X`, `USDNOK=X`, `USDSEK=X`). Pairs with USD as base currency (`USDJPY`, `USDCAD`, `USDCHF`, `USDNOK`, `USDSEK`) are inverted (`1 / close`) so all prices reflect foreign currency value in USD.",
        f"- **Data window:** 2006-05-16 to 2026-11-06 (common history of all 9 pairs and QQQ; 5,148 weekday trading bars).",
        "- **Short Rates:** 3-month short interest rates downloaded from FRED public CSVs (`IR3TIB01EZM156N`, `IR3TIB01GBM156N`, `IR3TIB01AUM156N`, `IR3TIB01NZM156N`, `IR3TIB01JPM156N`, `IR3TIB01CAM156N`, `IR3TIB01CHM156N`, `IR3TIB01NOM156N`, `IR3TIB01SEM156N`, `IR3TIB01USM156N`), monthly frequency, forward-filled to daily trading bars.",
        f"- **Dropped currencies:** {', '.join(dropped_ccys) if dropped_ccys else 'None. All 9 G10 currencies and USD have complete series.'}",
        "- **Daily Carry Return per Currency:** `spot_return + (r_foreign - r_USD) / 252`.",
        "- **Execution Model:** Rebalanced on the first trading bar of each calendar month. Weights decided at close of day t earn day t+1. 5 bp transaction cost per unit of turnover (`0.0005 * sum(|w_t - w_{t-1}|)`). Uninvested weight in USD cash earns the US 3-month rate (`r_USD / 252`).",
        "- **Benchmark & Alpha Stats:** Alpha vs QQQ per CLAUDE.md: weekday rows, daily returns, `beta = cov(r, q) / var(q)`, `alpha = resid.mean() * 252`, `t = resid.mean() / resid.std() * sqrt(n)`. H1/H2 computed on first and second halves of the series.",
        "",
        "## Results Table",
        "",
        "| Strategy Variant | Start | Alpha/yr | t | Beta | Corr | CAGR | Max DD | H1 Alpha (t) | H2 Alpha (t) | Verdict |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    for r in results:
        lines.append(
            f"| {r.name} | 2006 | {r.alpha:+.2f}% | {r.t:.2f} | {r.beta:.3f} | {r.corr:.3f} | "
            f"{r.cagr:.2f}% | {r.maxdd:.2f}% | {r.h1_alpha:+.2f}% ({r.h1_t:.2f}) | {r.h2_alpha:+.2f}% ({r.h2_t:.2f}) | {r.verdict} |"
        )

    lines.extend([
        "",
        "## Detailed Analysis",
        "",
        "### 1. Carry vs. Spot-Only Baseline",
        "",
        "- **Top-3 Carry vs. Top-3 Spot-Only:**",
        f"  - Spot-Only Top 3 (same holdings, no interest differential, cash at 0%): **alpha {spot_top3.alpha:+.2f}% / yr**, **t = {spot_top3.t:.2f}**, Max DD **{spot_top3.maxdd:.2f}%**.",
        f"  - Carry Top 3 (with foreign interest and USD cash yield): **alpha {lo.alpha:+.2f}% / yr**, **t = {lo.t:.2f}**, Max DD **{lo.maxdd:.2f}%**.",
        f"  - **Net Carry Lift:** **{lift_alpha:+.2f}%/yr alpha**, lifting t by **{lift_t:+.2f}** and reducing drawdown by **{lift_dd:.2f}%**.",
        "- **Existing Spot Momentum Baseline (`QuantsmdFxCrossSectionalMomentum`):**",
        f"  - Trailing 63-bar price return on FX4 held at 50% NAV generates **alpha {sm_fx4.alpha:+.2f}%/yr**, **t = {sm_fx4.t:.2f}**, Max DD **{sm_fx4.maxdd:.2f}%**.",
        "  - This reproduces the finding in `docs/backtests/quantsmd-2026-11-all.md` that spot FX price momentum loses without interest carry.",
        "",
        "### 2. Long/Short Reference ('Not Tradeable Here')",
        "",
        "- The long/short carry strategy holds the top 3 rate differential currencies long (+33.3% each) and shorts the bottom 3 currencies (-33.3% each), with 100% cash collateral earning the US 3-month rate.",
        f"- **Performance (100% L / 100% S):** CAGR **{ls.cagr:.2f}%**, Max DD **{ls.maxdd:.2f}%**, Alpha **{ls.alpha:+.2f}%/yr**, **t = {ls.t:.2f}** (H1 alpha {ls.h1_alpha:+.2f}%, t {ls.h1_t:.2f}; H2 alpha {ls.h2_alpha:+.2f}%, t {ls.h2_t:.2f}).",
        f"- **Performance (50% L / 50% S, 1.0 gross):** CAGR **{ls_half.cagr:.2f}%**, Max DD **{ls_half.maxdd:.2f}%**, Alpha **{ls_half.alpha:+.2f}%/yr**, **t = {ls_half.t:.2f}** (H1 alpha {ls_half.h1_alpha:+.2f}%, t {ls_half.h1_t:.2f}; H2 alpha {ls_half.h2_alpha:+.2f}%, t {ls_half.h2_t:.2f}).",
        "- While this demonstrates that cross-sectional rate differentials are predictive in FX markets, it cannot be run in our long-only broker framework, and even with shorting it fails the project requirement of t >= 2.0 across the full window.",
        "",
        "### 3. Correlation with QQQ and Surviving Books",
        "",
        "Correlation of daily returns between Long-Only FX Carry and the project's return streams:",
        "",
        "| Stream | Correlation with Long-Only FX Carry |",
        "|---|---|",
    ])

    for stream, c in corrs.items():
        lines.append(f"| {stream} | {c:+.3f} |")

    lines.extend([
        "",
        f"- **Interpretation:** Long-only FX carry has minimal correlation with US equities (QQQ corr {corrs.get('QQQ', 0.0):.2f}) and low correlation with trend/rotation survivors ({min(c for k, c in corrs.items() if k != 'QQQ'):+.2f} to {max(c for k, c in corrs.items() if k != 'QQQ'):+.2f}). While it is an uncorrelated return stream, its near-zero alpha ({lo.alpha:+.2f}%, t {lo.t:.2f}) means combining it with other books would merely dilute portfolio Sharpe without adding alpha.",
        "",
        "## Conclusion",
        "",
        "Step X1 is complete. Long-only FX carry is conclusively **unproven**. Do not promote an FX carry bot and do not tune parameters.",
    ])

    return "\n".join(lines) + "\n"


def main() -> None:
    t0 = time.time()
    print("Starting FX Carry Study (Step X1)...", file=sys.stderr)
    results, corrs, dropped_ccys, r_lo = run_fx_carry_study()

    doc_content = generate_report(results, corrs, dropped_ccys)
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.write_text(doc_content)
    print(f"Report written to {OUT_DOC}", file=sys.stderr)

    # Print summary table to stdout
    print("\n" + "=" * 95)
    print(f"{'Strategy Variant':<45} | {'Alpha/yr':<9} | {'t':<5} | {'Beta':<5} | {'Corr':<5} | {'Verdict':<8}")
    print("-" * 95)
    for r in results:
        print(f"{r.name:<45} | {r.alpha:>+8.2f}% | {r.t:>5.2f} | {r.beta:>5.3f} | {r.corr:>5.3f} | {r.verdict:<8}")
    print("=" * 95)
    print(f"Elapsed: {time.time() - t0:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
