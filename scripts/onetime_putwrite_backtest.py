"""
onetime_putwrite_backtest.py — Backtest and walk-forward tune option_PutWriteBot.

Backtest 1 (Synthetic, 2000 -> 2026, split 2013-06):
  Uses Black-Scholes pricing with calibrated skew (^VIX x 0.85, ^VXN x 0.90 for QQQ).
  Tests long-horizon performance through major market cycles (2000 tech bust, 2008 GFC, 2020 COVID).

Backtest 2 (Real Surface Replay, 2019-05 -> 2026-09):
  SurfaceReplayMarket(live_expiries=True) via utils/option_replay.run_strategy.
  Replays historical option chains and daily interpolated vol surfaces on real market quotes.

Walk-forward tuning:
  Uses scripts/option_tune_common.walk_forward:
  Pick on H1 by alpha_t vs QQQ, judge on H2.
  Ships tuned parameters only if H1 winner beats a-priori defaults on H2.
"""

from __future__ import annotations

import argparse
import datetime
import itertools
import logging
import math
import os
import pickle
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

from tradingbot.option_putwritebot import OptionPutWriteBot
from tradingbot.utils import option_math as om
from tradingbot.utils.db import OptionQuote, get_db_session
from tradingbot.utils.option_replay import (
    IRX,
    PriceHistory,
    SurfaceReplayMarket,
    run_strategy,
)
from tradingbot.utils.putwrite_rules import (
    PutWriteRules,
    decide_putwrite,
    short_premium_exit_reason,
)

logger = logging.getLogger("putwrite_backtest")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

SYNTHETIC_EVAL_START = "2000-03-10"
SYNTHETIC_SPLIT = "2013-06-01"
REAL_SURFACE_START = "2019-05-10"
REAL_SURFACE_END = "2026-09-25"
REAL_SURFACE_SPLIT = "2023-01-01"


# ------------------------------------------------------------------
# Cached Surface Replay Market
# ------------------------------------------------------------------


class CachedSurfaceReplayMarket(SurfaceReplayMarket):
    """SurfaceReplayMarket that caches filtered option_quotes locally to avoid slow port-forward transfers."""

    def __init__(
        self,
        start: date,
        end: date,
        underlyings: Iterable[str],
        cache_dir: str = "/tmp/putwrite_cache",
        live_expiries: bool = True,
    ):
        self._events: dict[str, Any] = {}
        self._macro = None
        self._derived: dict[str, Any] = {}
        self.caps = None
        self._spots: dict[str, pd.Series] = {}
        self.live_expiries = live_expiries
        self._surfaces: dict[tuple[str, date], Any] = {}
        self.recorded = self.modelled = 0

        underlyings_list = sorted(set(underlyings))
        os.makedirs(cache_dir, exist_ok=True)
        tag = "_".join(underlyings_list)
        cache_file = os.path.join(cache_dir, f"quotes_{tag}_{start}_{end}.pkl")

        if os.path.exists(cache_file):
            logger.info("Loading cached option_quotes from %s", cache_file)
            frame = pd.read_pickle(cache_file)
        else:
            logger.info("Querying option_quotes for %s in [%s, %s]", underlyings_list, start, end)
            lo = datetime.datetime.combine(start, datetime.time.min)
            hi = datetime.datetime.combine(end + timedelta(days=1), datetime.time.min)
            cols = list(OptionQuote.__table__.columns)
            col_names = [c.name for c in cols]
            parts = []
            with get_db_session() as session:
                for u in underlyings_list:
                    # ALWAYS filter by underlying and snapshot_at date range
                    rows = (
                        session.query(*cols)
                        .filter(
                            OptionQuote.underlying == u,
                            OptionQuote.snapshot_at >= lo,
                            OptionQuote.snapshot_at < hi,
                        )
                        .all()
                    )
                    if rows:
                        parts.append(pd.DataFrame(rows, columns=col_names))
            frame = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=col_names)
            if not frame.empty:
                logger.info("Saving %d quotes to cache %s", len(frame), cache_file)
                frame.to_pickle(cache_file)

        if frame.empty:
            self.frame = frame
            self._by_day: dict[tuple[str, date], pd.DataFrame] = {}
            return

        frame["day"] = pd.to_datetime(frame["snapshot_at"]).dt.date
        frame["expiry"] = pd.to_datetime(frame["expiration"]).dt.date
        frame = frame.sort_values("snapshot_at").drop_duplicates(["contract_symbol", "day"], keep="last")
        self.frame = frame
        self._by_day = {k: g.reset_index(drop=True) for k, g in frame.groupby(["underlying", "day"])}
        px = frame.assign(px=pd.to_numeric(frame["underlying_price"], errors="coerce")).dropna(subset=["px"])
        for u, g in px.groupby("underlying"):
            self._spots[u] = g.groupby("day")["px"].last().sort_index()


def load_cached_prices(
    symbols: Iterable[str],
    start: date,
    cache_dir: str = "/tmp/putwrite_cache",
) -> PriceHistory:
    """Download and cache daily OHLC price history."""
    os.makedirs(cache_dir, exist_ok=True)
    syms = sorted(set(symbols))
    tag = "_".join(syms).replace("^", "")
    cache_file = os.path.join(cache_dir, f"prices_{tag}_{start}.pkl")

    if os.path.exists(cache_file):
        logger.info("Loading cached price history from %s", cache_file)
        with open(cache_file, "rb") as f:
            ohlc_dict = pickle.load(f)
        return PriceHistory(ohlc_dict)

    logger.info("Downloading price history for %s since %s", syms, start)
    history = PriceHistory.download(syms, start, years_before=5)
    with open(cache_file, "wb") as f:
        pickle.dump(history._ohlc, f)
    return history


# ------------------------------------------------------------------
# Synthetic Simulation Pricing
# ------------------------------------------------------------------


@dataclass(frozen=True)
class CalibratedUnderlying:
    symbol: str
    vol_index: str
    atm_ratio: float
    put_skew: float
    call_skew: float


CALIBRATED = {
    "SPY": CalibratedUnderlying("SPY", "^VIX", 0.85, 0.28, 0.10),
    "QQQ": CalibratedUnderlying("QQQ", "^VXN", 0.90, 0.21, 0.07),
}


def load_synthetic_inputs(
    underlying: str = "SPY",
    cache_dir: str = "/tmp/putwrite_cache",
    start: date | None = None,
    end: date | None = None,
    is_smoke: bool = False,
) -> pd.DataFrame:
    """Prepare inputs for synthetic backtest (prices, vol indices, fair HAR vol)."""
    os.makedirs(cache_dir, exist_ok=True)
    tag = f"synth_inputs_{underlying}_{start}_{end}_{is_smoke}"
    path = os.path.join(cache_dir, f"{tag}.pkl")
    if os.path.exists(path):
        logger.info("Loading cached synthetic inputs from %s", path)
        with open(path, "rb") as f:
            return pickle.load(f)

    calib = CALIBRATED.get(underlying, CALIBRATED["SPY"])
    download_start = "2024-01-01" if is_smoke else "1999-01-01"
    logger.info("Downloading synthetic market inputs for %s since %s", underlying, download_start)

    raw = yf.download(underlying, start=download_start, auto_adjust=True, progress=False)
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw.index = pd.to_datetime(raw.index).tz_localize(None).normalize()

    df = pd.DataFrame(index=raw.index)
    df["S"] = raw["Close"]
    df["close"] = raw["Close"]
    df["high"] = raw["High"]
    df["low"] = raw["Low"]

    for sym, col in [
        ("QQQ", "qqq"),
        (calib.vol_index, "vol_index"),
        ("^VIX", "vix"),
        ("^VIX3M", "vix3m"),
        ("^IRX", "irx"),
    ]:
        d = yf.download(sym, start=download_start, auto_adjust=True, progress=False)
        if isinstance(d.columns, pd.MultiIndex):
            d.columns = d.columns.get_level_values(0)
        d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
        df[col] = d["Close"].reindex(df.index).ffill()

    df["r"] = (df["irx"] / 100.0).clip(lower=0).fillna(0.0)
    df["iv"] = (df["vol_index"] / 100.0) * calib.atm_ratio
    df["term_ratio"] = df["vix"] / df["vix3m"]
    df["sma200"] = df["S"].rolling(200, min_periods=20).mean()

    # Pre-calculate HAR RV forecast for DTE windows
    returns = om.log_returns(df["S"])
    from joblib import Parallel, delayed

    eval_indices = list(range(len(df)))
    for dte in (21, 35, 45):
        bdays = max(round(dte * 5 / 7), 1)
        if is_smoke or len(eval_indices) < 200:
            har = [om.har_rv_forecast(returns.iloc[:i], bdays) if i >= 10 else 0.15 for i in eval_indices]
        else:
            chunks = np.array_split(np.arange(len(eval_indices)), 16)

            def _calc_chunk(chunk, bdays=bdays):
                return [om.har_rv_forecast(returns.iloc[:i], bdays) if i >= 60 else 0.15 for i in chunk]

            parts = Parallel(n_jobs=-1)(delayed(_calc_chunk)(c) for c in chunks)
            har = [x for p in parts for x in p]
        df[f"fair_{dte}"] = har

    df = df.dropna(subset=["iv", "qqq", "sma200"])
    if start:
        df = df[df.index >= pd.Timestamp(start)]
    if end:
        df = df[df.index <= pd.Timestamp(end)]

    with open(path, "wb") as f:
        pickle.dump(df, f)
    return df


def sim_synthetic_putwrite(
    df: pd.DataFrame,
    rules: PutWriteRules,
    calib: CalibratedUnderlying | None = None,
    capital: float = 100_000.0,
) -> tuple[pd.Series, int]:
    """Run pure synthetic cash-secured put write simulation."""
    if calib is None:
        calib = CALIBRATED.get(rules.underlying, CALIBRATED["SPY"])

    cash = float(capital)
    trades = 0
    curve = {}

    # Open position state
    pos_strike: float | None = None
    pos_expiry: date | None = None
    pos_credit: float = 0.0
    pos_contracts: int = 0

    for ts, row in df.iterrows():
        today = ts.date()
        spot = float(row["S"])
        r = float(row["r"])

        # 1. Settle expiring options
        if pos_strike is not None and pos_expiry is not None and today >= pos_expiry:
            loss_per_share = max(pos_strike - spot, 0.0)
            cash -= loss_per_share * pos_contracts * 100.0
            pos_strike = pos_expiry = None
            pos_credit = 0.0
            pos_contracts = 0

        # Current marked value of short put
        cur_liability = 0.0
        if pos_strike is not None and pos_expiry is not None:
            dte = (pos_expiry - today).days
            if dte <= 0:
                cur_liability = max(pos_strike - spot, 0.0) * pos_contracts * 100.0
            else:
                T = dte / 365.25
                atm = float(row["iv"])
                # put skew
                z = math.log(pos_strike / spot) / (atm * math.sqrt(T)) if spot > 0 and atm > 0 else 0.0
                vol = max(atm * (1.0 - calib.put_skew * z), 0.05)
                opt_price = om.bs_price(spot, pos_strike, T, r, vol, "P")
                cur_liability = opt_price * pos_contracts * 100.0

            # 2. Check exit conditions
            pnl = pos_credit - cur_liability
            reason = short_premium_exit_reason(pos_credit, pnl, dte, rules.take_profit, rules.exit_dte)
            if reason:
                cash -= cur_liability
                pos_strike = pos_expiry = None
                pos_credit = 0.0
                pos_contracts = 0
                cur_liability = 0.0

        # 3. Check entry when flat
        if pos_strike is None:
            entry_ok = True

            # Gate 1: Curve slope
            if rules.max_term_ratio is not None:
                tr = float(row.get("term_ratio", np.nan))
                if np.isnan(tr) or tr > rules.max_term_ratio:
                    entry_ok = False

            # Gate 2: Trend filter
            if entry_ok and rules.trend_filter:
                sma200 = float(row.get("sma200", np.nan))
                if np.isnan(sma200) or spot <= sma200:
                    entry_ok = False

            # Gate 3: IV-fair gap
            if entry_ok and rules.min_gap is not None:
                fair_col = f"fair_{rules.target_dte}"
                fair = float(row.get(fair_col, row.get("fair_35", np.nan)))
                gap_thresh = rules.min_gap / 100.0 if rules.min_gap > 0.5 else rules.min_gap
                if np.isnan(fair) or (float(row["iv"]) - fair) < gap_thresh:
                    entry_ok = False

            if entry_ok:
                dte = rules.target_dte
                T = dte / 365.25
                atm = float(row["iv"])
                k_approx = om.strike_for_delta(spot, T, r, atm, rules.put_delta, "P")
                # One refinement pass for skew
                z = math.log(k_approx / spot) / (atm * math.sqrt(T)) if spot > 0 and atm > 0 else 0.0
                vol = max(atm * (1.0 - calib.put_skew * z), 0.05)
                strike = round(om.strike_for_delta(spot, T, r, vol, rules.put_delta, "P"), 1)
                premium = om.bs_price(spot, strike, T, r, vol, "P")

                strike_capital = strike * 100.0
                max_contracts = int(cash // strike_capital) if strike_capital > 0 else 0

                if max_contracts > 0:
                    pos_strike = strike
                    pos_expiry = today + timedelta(days=dte)
                    pos_credit = premium * max_contracts * 100.0
                    pos_contracts = max_contracts
                    cash += pos_credit
                    trades += 1
                    cur_liability = pos_credit

        equity = cash - cur_liability
        curve[ts] = equity

    return pd.Series(curve, dtype=float), trades


# ------------------------------------------------------------------
# Grid Search Setup
# ------------------------------------------------------------------


def generate_grid(names: list[str], max_grid: int | None = None) -> list[PutWriteRules]:
    """Generate economically motivated grid up to ~300 combinations."""
    combos = []
    # Always include a-priori defaults first
    defaults = OptionPutWriteBot.RULES
    combos.append(defaults)

    deltas = [0.10, 0.16, 0.25, 0.30]
    dtes = [21, 35, 45]
    term_ratios = [None, 1.0, 0.95]
    trends = [False, True]
    min_gaps = [None, 0.03]
    tps = [None, 0.50]
    exit_dtes = [7, 14, 21]

    for name in names:
        for delta, dte, tr, trend, gap, tp, edte in itertools.product(
            deltas, dtes, term_ratios, trends, min_gaps, tps, exit_dtes
        ):
            r = PutWriteRules(
                underlying=name,
                put_delta=delta,
                target_dte=dte,
                max_term_ratio=tr,
                trend_filter=trend,
                min_gap=gap,
                take_profit=tp,
                exit_dte=edte,
            )
            if r != defaults:
                combos.append(r)
            if max_grid and len(combos) >= max_grid:
                return combos

    return combos[:max_grid] if max_grid else combos


# ------------------------------------------------------------------
# Report Markdown Generation
# ------------------------------------------------------------------


def write_markdown_report(
    out_path: str,
    defaults_row: dict[str, Any],
    winner_row: dict[str, Any],
    top10_mean_h2_t: float,
    share_pos_h2: float,
    shipped: str,
    summary_table_md: str,
    real_summary_md: str,
    grid_size: int,
    is_smoke: bool = False,
) -> None:
    """Generate the structured markdown artifact docs/backtests/putwrite-2026-11.md."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    smoke_banner = (
        "> [!NOTE]\n> **Smoke Test Run**: Executed on a fast sample to verify end-to-end execution.\n\n"
        if is_smoke
        else ""
    )

    content = f"""# option_PutWriteBot Walk-Forward Backtest & Tuning (2026-11)

{smoke_banner}## Executive Summary

- **Strategy**: Cash-secured short put writing on index ETFs (SPY / QQQ) with curve-slope (^VIX/^VIX3M) and trend (SMA200) gating.
- **A-Priori Defaults**: SPY, 0.25Δ, 35 DTE, ungated, 50% take profit / 21 DTE exit.
- **Tuning Objective**: Walk-forward alpha t-statistic vs QQQ (pick on H1, judge on unseen H2).
- **Ship Decision**: **{shipped}**

## Strategy Levers

| Lever | A-Priori Default | Grid Values |
|---|---|---|
| Underlying | SPY | SPY, QQQ |
| Put Delta | 0.25 | 0.10, 0.16, 0.25, 0.30 |
| Target DTE | 35 | 21, 35, 45 |
| Max VIX/VIX3M Gate | None (ungated) | None, 1.0, 0.95 |
| Trend Filter | False (off) | False, True (close > SMA200) |
| Min IV-Fair Gap | None (ungated) | None, 0.0, 3 pts (0.03) |
| Take Profit | 0.50 (50% credit) | None, 0.50 |
| Exit DTE | 21 | 7, 14, 21 |

## Walk-Forward Tuning Summary (Synthetic 2000 → 2026)

- **Total Combinations Evaluated**: {grid_size}
- **Top-10 Mean H2 t**: {top10_mean_h2_t:.2f}
- **Share of Grid with H2 t > 0**: {share_pos_h2:.1%}

{summary_table_md}

## Real Surface Replay Results (2019-05 → 2026-09)

Replay against historical quotes and daily vol surfaces via `SurfaceReplayMarket(live_expiries=True)`.

{real_summary_md}

## Detailed Performance Comparison

### Defaults Configuration
- **Rules**: `{defaults_row.get("params", "SPY 0.25d 35dte")}`
- **H1 Alpha**: {defaults_row.get("H1_alpha", np.nan):.2%} (t: {defaults_row.get("H1_t", np.nan):.2f})
- **H2 Alpha**: {defaults_row.get("H2_alpha", np.nan):.2%} (t: {defaults_row.get("H2_t", np.nan):.2f}, Beta: {defaults_row.get("H2_beta", np.nan):.2f}, Sharpe: {defaults_row.get("H2_sharpe", np.nan):.2f})
- **Full Window**: CAGR {defaults_row.get("full_true_cagr", np.nan):.2%}, Max DD {defaults_row.get("full_max_dd", np.nan):.2%}, Trades: {defaults_row.get("full_trades", 0)}

### H1 Winner Configuration
- **Rules**: `{winner_row.get("params", "Winner")}`
- **H1 Alpha**: {winner_row.get("H1_alpha", np.nan):.2%} (t: {winner_row.get("H1_t", np.nan):.2f})
- **H2 Alpha**: {winner_row.get("H2_alpha", np.nan):.2%} (t: {winner_row.get("H2_t", np.nan):.2f}, Beta: {winner_row.get("H2_beta", np.nan):.2f}, Sharpe: {winner_row.get("H2_sharpe", np.nan):.2f})
- **Full Window**: CAGR {winner_row.get("full_true_cagr", np.nan):.2%}, Max DD {winner_row.get("full_max_dd", np.nan):.2%}, Trades: {winner_row.get("full_trades", 0)}

## Protocol Decision & Live Rules

According to the walk-forward protocol:
1. Candidate chosen on H1 by alpha t vs QQQ.
2. Judged on out-of-sample H2.
3. Shipped tuned values only if H1 winner beats defaults on H2.

Result: **{shipped}** (`OptionPutWriteBot.RULES` set accordingly).
"""
    with open(out_path, "w") as f:
        f.write(content)
    logger.info("Wrote markdown report to %s", out_path)


# ------------------------------------------------------------------
# Main CLI & Runner
# ------------------------------------------------------------------


def parse_args():
    parser = argparse.ArgumentParser(description="option_PutWriteBot backtest and tuning study")
    parser.add_argument("--names", default="SPY", help="Comma-separated underlying symbols (e.g. SPY or SPY,QQQ)")
    parser.add_argument("--start", default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument("--max-grid", type=int, default=None, help="Max grid combinations to evaluate")
    parser.add_argument("--out", default="docs/backtests/putwrite-2026-11.md", help="Output markdown file path")
    parser.add_argument("--grid-csv", default="/tmp/putwrite_grid_2026_11.csv", help="Output CSV for full grid")
    parser.add_argument("--cache-dir", default="/tmp/putwrite_cache", help="Directory to cache queries and prices")
    parser.add_argument(
        "--mode",
        choices=["all", "synthetic", "replay", "smoke"],
        default="all",
        help="Run mode (smoke limits to tiny fast sample)",
    )
    parser.add_argument("--smoke", action="store_true", help="Shortcut for smoke test (<=60s end-to-end)")
    parser.add_argument("--n-jobs", type=int, default=1, help="Parallel workers for grid evaluation")
    return parser.parse_args()


def main():
    args = parse_args()
    is_smoke = args.smoke or args.mode == "smoke"
    names = [s.strip().upper() for s in args.names.split(",") if s.strip()]

    max_grid = args.max_grid or (4 if is_smoke else None)
    cache_dir = args.cache_dir
    os.makedirs(cache_dir, exist_ok=True)

    logger.info("Starting option_PutWriteBot study (smoke=%s, names=%s, max_grid=%s)", is_smoke, names, max_grid)

    from scripts.option_tune_common import summary_table, walk_forward

    # 1. Synthetic Backtest & Grid Tuning
    logger.info("Running synthetic simulation inputs...")
    u = names[0]
    start_date = date.fromisoformat(args.start) if args.start else None
    end_date = date.fromisoformat(args.end) if args.end else None
    if is_smoke and not start_date:
        start_date = date(2026, 8, 1)
    if is_smoke and not end_date:
        end_date = date(2026, 9, 25)

    synth_df = load_synthetic_inputs(u, cache_dir=cache_dir, start=start_date, end=end_date, is_smoke=is_smoke)
    split = synth_df.index[len(synth_df) // 2] if is_smoke else SYNTHETIC_SPLIT

    combos = generate_grid(names, max_grid=max_grid)
    logger.info("Evaluating %d combinations in walk-forward grid...", len(combos))

    def run_combo(r: PutWriteRules):
        curve, trades = sim_synthetic_putwrite(synth_df, r)
        return {"curve": curve, "trades": trades, "benchmark": synth_df["qqq"]}

    wf_results = walk_forward(
        combos,
        run_combo,
        split=split,
        rank_key="alpha_t",
        defaults=OptionPutWriteBot.RULES,
        bot_name="option_PutWriteBot",
        benchmark=synth_df["qqq"],
        n_jobs=args.n_jobs,
        verbose=False,
    )

    # Save full grid CSV
    os.makedirs(os.path.dirname(os.path.abspath(args.grid_csv)), exist_ok=True)
    wf_results.to_csv(args.grid_csv, index=False)
    logger.info("Saved grid CSV to %s", args.grid_csv)

    defaults_row = wf_results.iloc[0].to_dict()
    h1_winner_idx = wf_results["H1_alpha_t"].fillna(-999).argmax()
    winner_row = wf_results.iloc[h1_winner_idx].to_dict()

    ranked = wf_results.sort_values(by="H1_alpha_t", ascending=False)
    top10_mean_h2_t = float(ranked["H2_alpha_t"].head(10).mean())
    valid_h2 = wf_results["H2_alpha_t"].dropna()
    share_pos_h2 = float((valid_h2 > 0).mean()) if len(valid_h2) > 0 else 0.0

    from scripts.option_tune_common import ship_decision

    shipped = ship_decision(defaults_row, winner_row)
    sum_df = summary_table(
        wf_results, bot_name="option_PutWriteBot", defaults_row=defaults_row, h1_winner_row=winner_row
    )
    summary_table_md = sum_df.to_markdown(index=False)

    # 2. Real Surface Replay Check
    real_summary_md = ""
    if args.mode in ("all", "replay", "smoke"):
        start_d = (
            date(2026, 8, 1) if is_smoke else (date.fromisoformat(args.start) if args.start else date(2019, 5, 10))
        )
        end_d = date(2026, 8, 15) if is_smoke else (date.fromisoformat(args.end) if args.end else date(2026, 9, 25))

        logger.info("Initializing SurfaceReplayMarket for %s [%s to %s]...", names, start_d, end_d)
        try:
            market = CachedSurfaceReplayMarket(
                start=start_d,
                end=end_d,
                underlyings=names,
                cache_dir=cache_dir,
                live_expiries=True,
            )
            if market.days:
                prices = load_cached_prices(
                    set(names) | {"QQQ", "^VIX", "^VIX3M", IRX},
                    market.days[0],
                    cache_dir=cache_dir,
                )
                curve, book = run_strategy(
                    market,
                    lambda m, h: decide_putwrite(m, h, OptionPutWriteBot.RULES, names[0]),
                    prices,
                    fill="quote",
                )
                qqq_curve = prices.upto("QQQ", market.days[-1])["close"]
                from scripts.option_tune_common import metrics

                m = metrics(curve, qqq_curve)
                real_summary_md = f"""| Underlying | Days | Trades | CAGR | Alpha/yr | t | Beta | Max DD |
|---|---|---|---|---|---|---|---|
| {names[0]} | {len(market.days)} | {book.trades} | {m.get("true_cagr", np.nan):.2%} | {m.get("alpha", np.nan):.2%} | {m.get("t", np.nan):.2f} | {m.get("beta", np.nan):.2f} | {m.get("max_dd", np.nan):.2%} |
"""
            else:
                real_summary_md = "No recorded option quotes found in the specified range."
        except Exception as exc:
            logger.warning("Surface replay failed: %s", exc)
            real_summary_md = f"Surface replay skipped or failed: {exc}"

    # 3. Write Markdown Artifact
    write_markdown_report(
        out_path=args.out,
        defaults_row=defaults_row,
        winner_row=winner_row,
        top10_mean_h2_t=top10_mean_h2_t,
        share_pos_h2=share_pos_h2,
        shipped=shipped,
        summary_table_md=summary_table_md,
        real_summary_md=real_summary_md,
        grid_size=len(combos),
        is_smoke=is_smoke,
    )
    logger.info("Backtest study finished successfully.")


if __name__ == "__main__":
    main()
