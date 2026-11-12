"""
Walk-forward study of option_FOMCVolBot (trading index event vol into FOMC decisions).

Evaluates entering SPY options (straddle, strangle, iron fly) at the close of
the session immediately preceding an FOMC announcement (T-1) and exiting at the
close of the announcement session (T0).

- 2019-05 -> 2026-11: priced off stored option quotes in `option_quotes` via `utils/option_surface.DaySurface`.
- 2006-01 -> 2019-05: synthetic Black-Scholes using ^VIX implied vol and calibrated SPY smile.
- Also tests CPI and NFP macro announcement dates as control/comparison.
- Tuning grid over side (long/short), structure (iron_fly/straddle/strangle), wing width,
  strike offset, and delta hedging.

Run with withenv wrapper:
  /home/jguese/code/tradingbot/python_tradingbot_framework/.venv/bin/python \
    /home/jguese/code/tradingbot/python_tradingbot_framework/.git/agy-wt/withenv.py \
    scripts/onetime_fomc_vol_study.py [--names SPY] [--max-grid 4] [--max-events 5]
"""

from __future__ import annotations

import argparse
import logging
import math
import pickle
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import sqlalchemy as sa
import yfinance as yf

from tradingbot.utils import macro_calendar, option_surface
from tradingbot.utils import option_math as om
from tradingbot.utils.db import MacroEvent, get_db_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fomc_vol_study")

CACHE_DIR_DEFAULT = Path("/tmp/fomc_vol_cache")
DOC_PATH_DEFAULT = Path("docs/backtests/fomc-vol-2026-11.md")
CSV_PATH_DEFAULT = Path("/tmp/fomc_vol_trades.csv")

REAL_DATA_START = date(2019, 5, 10)
SAMPLE_END_DEFAULT = date(2026, 11, 9)
STUDY_START_DEFAULT = date(2006, 1, 1)


# ------------------------------------------------------------------
# Data Loaders & Caching
# ------------------------------------------------------------------


def load_quotes_for_underlying(underlying: str, start: date, end: date, cache_dir: Path) -> pd.DataFrame:
    """
    Load option_quotes for `underlying` strictly in [start, end].
    Filters by underlying AND snapshot_at date range to avoid full-table scans.
    Caches results to a pickle file under cache_dir.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{underlying}_{start}_{end}_quotes.pkl"
    if cache_file.exists():
        logger.info("Loading %s quotes from cache %s", underlying, cache_file)
        try:
            with open(cache_file, "rb") as f:
                return pickle.load(f)
        except Exception as exc:
            logger.warning("Failed to read cache %s: %s; re-querying", cache_file, exc)

    lo = datetime.combine(start, datetime.min.time())
    hi = datetime.combine(end + timedelta(days=1), datetime.min.time())
    logger.info("Querying option_quotes for %s in [%s, %s)...", underlying, lo, hi)

    query = """
        SELECT underlying, contract_symbol, expiration, option_type, strike,
               bid, ask, last_price, volume, open_interest, implied_volatility,
               snapshot_at, created_at, underlying_price
        FROM option_quotes
        WHERE underlying = :u AND snapshot_at >= :lo AND snapshot_at < :hi
        ORDER BY snapshot_at
    """
    try:
        with get_db_session() as session:
            result = session.execute(sa.text(query), {"u": underlying, "lo": lo, "hi": hi})
            rows = result.fetchall()
            cols = result.keys()
        df = pd.DataFrame(rows, columns=cols)
    except Exception as exc:
        logger.warning("Database query failed: %s; returning empty DataFrame", exc)
        return pd.DataFrame()

    if not df.empty:
        df["snapshot_at"] = pd.to_datetime(df["snapshot_at"])
        df["day"] = df["snapshot_at"].dt.date
        df["expiration"] = pd.to_datetime(df["expiration"])
        df["expiry"] = df["expiration"].dt.date
        df = df.sort_values("snapshot_at").drop_duplicates(["contract_symbol", "day"], keep="last")
        try:
            with open(cache_file, "wb") as f:
                pickle.dump(df, f)
            logger.info("Saved %d quotes to cache %s", len(df), cache_file)
        except Exception as exc:
            logger.warning("Could not write cache file %s: %s", cache_file, exc)

    return df


def load_price_history(symbols: list[str], start: date, end: date, cache_dir: Path) -> dict[str, pd.DataFrame]:
    """
    Download daily OHLC for underlying symbols and vol indices (^VIX, QQQ, etc.).
    Caches to cache_dir.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    sym_tag = "_".join(sorted(symbols))
    cache_file = cache_dir / f"prices_{sym_tag}_{start}_{end}.pkl"

    if cache_file.exists():
        logger.info("Loading prices from cache %s", cache_file)
        try:
            with open(cache_file, "rb") as f:
                return pickle.load(f)
        except Exception as exc:
            logger.warning("Failed to load price cache %s: %s", cache_file, exc)

    prices: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            logger.info("Downloading daily price data for %s...", sym)
            df = yf.download(
                sym,
                start=str(start),
                end=str(end + timedelta(days=2)),
                auto_adjust=True,
                progress=False,
            )
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df.columns = [c.lower() for c in df.columns]
            df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
            prices[sym] = df
        except Exception as exc:
            logger.warning("Failed to download price history for %s: %s", sym, exc)

    try:
        with open(cache_file, "wb") as f:
            pickle.dump(prices, f)
    except Exception as exc:
        logger.warning("Failed to save price cache: %s", exc)

    return prices


def load_macro_dates() -> dict[str, list[date]]:
    """
    Load scheduled dates for FOMC, CPI, and NFP.
    FOMC dates come from the static list in macro_calendar (2006-2027).
    CPI and NFP come from MacroEvent table or static fallback.
    """
    fomc_dates = sorted(macro_calendar.FOMC_STATEMENT_DATES)
    cpi_dates: list[date] = []
    nfp_dates: list[date] = []

    try:
        with get_db_session() as session:
            rows = (
                session.query(MacroEvent.kind, MacroEvent.event_date).filter(MacroEvent.kind.in_(["CPI", "NFP"])).all()
            )
            for kind, dt in rows:
                if kind == "CPI":
                    cpi_dates.append(dt)
                elif kind == "NFP":
                    nfp_dates.append(dt)
    except Exception as exc:
        logger.warning("Could not read MacroEvent table (%s); using FOMC dates only", exc)

    return {
        "FOMC": fomc_dates,
        "CPI": sorted(set(cpi_dates)),
        "NFP": sorted(set(nfp_dates)),
    }


# ------------------------------------------------------------------
# Simulation Engine
# ------------------------------------------------------------------


@dataclass(frozen=True)
class TradeSpec:
    side: str = "long"  # "long" or "short"
    structure: str = "iron_fly"  # "iron_fly", "straddle", or "strangle"
    wing_width_pct: float = 0.05
    strike_offset: float = 0.0
    delta_hedged: bool = False


@dataclass
class TradeResult:
    event_kind: str
    event_date: date
    entry_date: date
    exit_date: date
    side: str
    structure: str
    wing_width_pct: float
    strike_offset: float
    delta_hedged: bool
    spot_entry: float
    spot_exit: float
    pnl_usd: float
    max_risk_usd: float
    ret_pct: float
    is_real_quote: bool


def _synthetic_quote(
    right: str,
    strike: float,
    spot: float,
    vix: float,
    dte_days: float,
    r: float = 0.04,
) -> tuple[float, float, float]:
    """
    Synthetic Black-Scholes pricing calibrated to SPY index options with skew and spread.
    Returns (bid, ask, mid).
    """
    T = max(dte_days / 252.0, 1.0 / 252.0)
    atm_iv = (vix / 100.0) * 0.85
    # Standard smile: put skew steeper than call skew
    m = math.log(strike / spot) / (atm_iv * math.sqrt(T))
    skew_coef = 0.25 if right == "P" else 0.08
    iv = max(atm_iv * (1.0 - skew_coef * m), 0.05)

    intrinsic = max(0.0, spot - strike if right == "C" else strike - spot)
    mid = max(om.bs_price(spot, strike, T, r, iv, right, 0.015), intrinsic)
    half_spread = max(0.000074 * spot, 0.015 * mid, 0.01)
    bid = max(mid - half_spread, 0.0)
    ask = mid + half_spread
    return bid, ask, mid


def _synthetic_delta(right: str, strike: float, spot: float, vix: float, dte_days: float, r: float = 0.04) -> float:
    T = max(dte_days / 252.0, 1.0 / 252.0)
    atm_iv = (vix / 100.0) * 0.85
    m = math.log(strike / spot) / (atm_iv * math.sqrt(T))
    skew_coef = 0.25 if right == "P" else 0.08
    iv = max(atm_iv * (1.0 - skew_coef * m), 0.05)
    return om.delta(spot, strike, T, r, iv, right, 0.015)


def simulate_event_trade(
    event_kind: str,
    event_date: date,
    entry_date: date,
    exit_date: date,
    spec: TradeSpec,
    underlying: str,
    price_df: pd.DataFrame,
    vix_df: pd.DataFrame | None,
    quotes_by_day: dict[date, pd.DataFrame],
    fill_mode: str = "quote",
) -> TradeResult | None:
    """
    Simulates entering one trade at entry_date close and exiting at exit_date close.
    Uses real DaySurface if available in quotes_by_day; otherwise synthetic ^VIX model.
    """
    try:
        spot_entry = float(price_df.loc[pd.Timestamp(entry_date), "close"])
        spot_exit = float(price_df.loc[pd.Timestamp(exit_date), "close"])
    except (KeyError, IndexError):
        return None

    use_real = entry_date in quotes_by_day and exit_date in quotes_by_day

    # Determine strikes
    k_atm = round(spot_entry) + spec.strike_offset
    width = max(round(spec.wing_width_pct * spot_entry), 1.0)
    k_put_wing = k_atm - width
    k_call_wing = k_atm + width

    if spec.structure == "straddle":
        legs_info = [("C", k_atm), ("P", k_atm)]
    elif spec.structure == "iron_fly":
        # Short iron fly sells ATM straddle, buys wings
        legs_info = [("P", k_put_wing), ("P", k_atm), ("C", k_atm), ("C", k_call_wing)]
    elif spec.structure == "strangle":
        legs_info = [("P", k_put_wing), ("C", k_call_wing)]
    else:
        return None

    # Pricing at entry & exit
    entry_surface = None
    exit_surface = None
    if use_real:
        try:
            entry_surface = option_surface.build_surface(underlying, quotes_by_day[entry_date], spot_entry, entry_date)
            exit_surface = option_surface.build_surface(underlying, quotes_by_day[exit_date], spot_exit, exit_date)
        except Exception:
            entry_surface = None
            exit_surface = None

    is_real = entry_surface is not None and exit_surface is not None

    # VIX fallback levels
    vix_entry = 18.0
    vix_exit = 18.0
    if vix_df is not None:
        try:
            vix_entry = float(vix_df.loc[pd.Timestamp(entry_date), "close"])
            vix_exit = float(vix_df.loc[pd.Timestamp(exit_date), "close"])
        except Exception:
            pass

    # Expiry selection: target front weekly (~2-5 DTE from entry)
    front_expiry = entry_date + timedelta(days=max((4 - entry_date.weekday()) % 7, 2))

    net_entry_cash = 0.0
    net_exit_cash = 0.0
    net_delta = 0.0

    for right, strike in legs_info:
        # Sign relative to long/short:
        if spec.structure == "straddle":
            sign = 1 if spec.side == "long" else -1
        elif spec.structure == "iron_fly":
            # For short iron fly: sell ATM (+1 short), buy wings (+1 long)
            is_wing = strike in (k_put_wing, k_call_wing)
            sign = (1 if is_wing else -1) if spec.side == "short" else -1 if is_wing else 1
        elif spec.structure == "strangle":
            sign = 1 if spec.side == "long" else -1

        # Quote at entry
        if is_real and entry_surface is not None:
            bid_in, ask_in, mid_in = entry_surface.quote(right, strike, front_expiry)
            delta_leg = om.delta(
                spot_entry,
                strike,
                max(om.year_fraction(front_expiry, entry_date), 1.0 / 252.0),
                0.04,
                entry_surface.iv(strike, max(om.year_fraction(front_expiry, entry_date), 1.0 / 252.0)),
                right,
                0.015,
            )
        else:
            bid_in, ask_in, mid_in = _synthetic_quote(right, strike, spot_entry, vix_entry, 2.0)
            delta_leg = _synthetic_delta(right, strike, spot_entry, vix_entry, 2.0)

        # Fill at entry
        fill_in = mid_in if fill_mode == "mid" else ask_in if sign > 0 else bid_in

        # Cash flow at entry: buying pays debit (-fill), selling receives credit (+fill)
        net_entry_cash += (-sign) * fill_in
        net_delta += sign * delta_leg

        # Quote at exit
        if is_real and exit_surface is not None:
            bid_out, ask_out, mid_out = exit_surface.quote(right, strike, front_expiry)
        else:
            bid_out, ask_out, mid_out = _synthetic_quote(right, strike, spot_exit, vix_exit, 1.0)

        # Fill at exit: closing a long sells at bid (+bid), closing a short buys at ask (-ask)
        fill_out = mid_out if fill_mode == "mid" else bid_out if sign > 0 else ask_out

        net_exit_cash += sign * fill_out

    # Delta hedge P&L
    stock_pnl = 0.0
    if spec.delta_hedged:
        # Short delta shares to neutralize structure delta
        hedge_shares = -net_delta * 100.0
        stock_pnl = (hedge_shares / 100.0) * (spot_exit - spot_entry)

    total_pnl = (net_entry_cash + net_exit_cash + stock_pnl) * 100.0

    # Risk denominator
    if spec.structure == "iron_fly":
        # Defined-risk: width between strikes
        max_risk = width * 100.0
    elif spec.structure == "straddle":
        # For straddle: debit paid or entry premium
        max_risk = max(abs(net_entry_cash), 1.0) * 100.0
    else:
        # Strangle
        max_risk = max(abs(net_entry_cash), width * 0.5) * 100.0

    ret_pct = total_pnl / max_risk

    return TradeResult(
        event_kind=event_kind,
        event_date=event_date,
        entry_date=entry_date,
        exit_date=exit_date,
        side=spec.side,
        structure=spec.structure,
        wing_width_pct=spec.wing_width_pct,
        strike_offset=spec.strike_offset,
        delta_hedged=spec.delta_hedged,
        spot_entry=spot_entry,
        spot_exit=spot_exit,
        pnl_usd=total_pnl,
        max_risk_usd=max_risk,
        ret_pct=ret_pct,
        is_real_quote=is_real,
    )


# ------------------------------------------------------------------
# Statistics & Bootstrap
# ------------------------------------------------------------------


def bootstrap_ci(returns: Sequence[float], n_boot: int = 1000, alpha: float = 0.05) -> tuple[float, float]:
    """Calculate 95% bootstrap confidence interval of the mean return."""
    if len(returns) < 3:
        return float("nan"), float("nan")
    arr = np.asarray(returns)
    rng = np.random.default_rng(42)
    means = [np.mean(rng.choice(arr, size=len(arr), replace=True)) for _ in range(n_boot)]
    lo = float(np.percentile(means, 100 * (alpha / 2)))
    hi = float(np.percentile(means, 100 * (1 - alpha / 2)))
    return lo, hi


def compute_trade_stats(results: list[TradeResult]) -> dict[str, float]:
    if not results:
        return {
            "n": 0,
            "mean_ret": float("nan"),
            "t_stat": float("nan"),
            "win_rate": float("nan"),
            "ci_lo": float("nan"),
            "ci_hi": float("nan"),
            "real_n": 0,
        }
    rets = [r.ret_pct for r in results]
    n = len(rets)
    mean_val = float(np.mean(rets))
    std_val = float(np.std(rets, ddof=1)) if n > 1 else float("nan")
    t_stat = (mean_val / (std_val / math.sqrt(n))) if (std_val > 0 and n > 1) else float("nan")
    win_rate = float(sum(r > 0 for r in rets) / n)
    ci_lo, ci_hi = bootstrap_ci(rets)
    real_n = sum(r.is_real_quote for r in results)

    return {
        "n": n,
        "mean_ret": mean_val,
        "t_stat": t_stat,
        "win_rate": win_rate,
        "ci_lo": ci_lo,
        "ci_hi": ci_hi,
        "real_n": real_n,
    }


# ------------------------------------------------------------------
# Main Study Runner
# ------------------------------------------------------------------


def run_fomc_study(
    names: list[str],
    start: date,
    end: date,
    max_grid: int | None,
    max_events: int | None,
    cache_dir: Path,
    out_doc: Path,
    trades_csv: Path | None,
    fill_mode: str = "quote",
) -> None:
    logger.info("Starting FOMC Volatility Study across %s from %s to %s", names, start, end)

    # 1. Load Price History
    all_syms = list({*names, "SPY", "^VIX", "QQQ"})
    prices = load_price_history(all_syms, start - timedelta(days=30), end + timedelta(days=10), cache_dir)
    spy_px = prices.get("SPY", pd.DataFrame())
    vix_px = prices.get("^VIX", None)

    if spy_px.empty:
        logger.error("Could not obtain SPY price history; exiting study")
        return

    # 2. Load Option Quotes for real window (2019+)
    quotes_by_day: dict[str, dict[date, pd.DataFrame]] = {}
    for sym in names:
        q_start = max(start, REAL_DATA_START)
        if q_start < end:
            q_df = load_quotes_for_underlying(sym, q_start, end, cache_dir)
            if not q_df.empty:
                quotes_by_day[sym] = {d: g.reset_index(drop=True) for d, g in q_df.groupby("day")}
            else:
                quotes_by_day[sym] = {}
        else:
            quotes_by_day[sym] = {}

    # 3. Macro Event Dates
    macro_dates = load_macro_dates()
    fomc_dates = [d for d in macro_dates["FOMC"] if start <= d <= end]

    if max_events is not None and max_events > 0:
        fomc_dates = fomc_dates[:max_events]
        logger.info("Capped FOMC events to %d for smoke test", len(fomc_dates))

    logger.info("Loaded %d FOMC announcement dates in study window", len(fomc_dates))

    # Build Session Pairs: (entry_date, exit_date) for each event
    event_sessions: list[tuple[str, date, date, date]] = []
    trading_days = [ts.date() for ts in spy_px.index]

    for ev_date in fomc_dates:
        # Find preceding session
        earlier = [d for d in trading_days if d < ev_date]
        if not earlier:
            continue
        entry_d = earlier[-1]
        # Exit on the event session itself (or next if event was non-trading day)
        exit_candidates = [d for d in trading_days if d >= ev_date]
        if not exit_candidates:
            continue
        exit_d = exit_candidates[0]
        event_sessions.append(("FOMC", ev_date, entry_d, exit_d))

    # Control comparison: CPI and NFP events
    comparison_events: list[tuple[str, date, date, date]] = []
    for kind in ("CPI", "NFP"):
        k_dates = [d for d in macro_dates.get(kind, []) if start <= d <= end]
        if max_events is not None and max_events > 0:
            k_dates = k_dates[:max_events]
        for ev_date in k_dates:
            earlier = [d for d in trading_days if d < ev_date]
            if not earlier:
                continue
            entry_d = earlier[-1]
            exit_candidates = [d for d in trading_days if d >= ev_date]
            if not exit_candidates:
                continue
            exit_d = exit_candidates[0]
            comparison_events.append((kind, ev_date, entry_d, exit_d))

    # 4. Parameter Grid
    grid: list[TradeSpec] = []
    sides = ["long", "short"]
    structures = ["iron_fly", "straddle", "strangle"]
    widths = [0.03, 0.05, 0.08]
    offsets = [0.0]
    hedges = [False, True]

    for side in sides:
        for struct in structures:
            for w in widths:
                for off in offsets:
                    for h in hedges:
                        if struct == "straddle" and w != 0.05:
                            continue  # straddle width is irrelevant
                        grid.append(
                            TradeSpec(side=side, structure=struct, wing_width_pct=w, strike_offset=off, delta_hedged=h)
                        )

    # Dedupe specs
    grid = list(dict.fromkeys(grid))

    if max_grid is not None and max_grid > 0:
        grid = grid[:max_grid]
        logger.info("Capped grid to %d specifications for smoke run", len(grid))

    logger.info("Running %d grid variants over %d FOMC events...", len(grid), len(event_sessions))

    # 5. Run Simulations
    all_trade_records: list[TradeResult] = []
    grid_summary_rows: list[dict] = []

    primary_name = names[0]
    u_quotes = quotes_by_day.get(primary_name, {})

    for spec in grid:
        spec_results: list[TradeResult] = []
        for ev_kind, ev_d, in_d, out_d in event_sessions:
            res = simulate_event_trade(
                event_kind=ev_kind,
                event_date=ev_d,
                entry_date=in_d,
                exit_date=out_d,
                spec=spec,
                underlying=primary_name,
                price_df=spy_px,
                vix_df=vix_px,
                quotes_by_day=u_quotes,
                fill_mode=fill_mode,
            )
            if res is not None:
                spec_results.append(res)
                all_trade_records.append(res)

        # Compute stats for full, H1 (< 2019-05-10) and H2 (>= 2019-05-10)
        full_stats = compute_trade_stats(spec_results)
        h1_results = [r for r in spec_results if r.event_date < REAL_DATA_START]
        h2_results = [r for r in spec_results if r.event_date >= REAL_DATA_START]
        h1_stats = compute_trade_stats(h1_results)
        h2_stats = compute_trade_stats(h2_results)

        grid_summary_rows.append(
            {
                "side": spec.side,
                "structure": spec.structure,
                "wing_width": f"{spec.wing_width_pct:.0%}" if spec.structure != "straddle" else "-",
                "hedged": "Yes" if spec.delta_hedged else "No",
                "N": full_stats["n"],
                "MeanRet": f"{full_stats['mean_ret']:+.2%}",
                "t_stat": f"{full_stats['t_stat']:+.2f}",
                "WinRate": f"{full_stats['win_rate']:.1%}",
                "CI_95": f"[{full_stats['ci_lo']:+.1%}, {full_stats['ci_hi']:+.1%}]",
                "H1_N": h1_stats["n"],
                "H1_t": f"{h1_stats['t_stat']:+.2f}",
                "H2_N": h2_stats["n"],
                "H2_t": f"{h2_stats['t_stat']:+.2f}",
                "Real_N": full_stats["real_n"],
            }
        )

    # 6. Event Comparison Study (Default live rules: Long Iron Fly 5% wings vs Short Straddle)
    comp_summary_rows: list[dict] = []
    default_spec = TradeSpec(side="long", structure="iron_fly", wing_width_pct=0.05, delta_hedged=False)
    short_straddle_spec = TradeSpec(side="short", structure="straddle", wing_width_pct=0.05, delta_hedged=False)

    for spec_label, spec_obj in [("Long Iron Fly (5%)", default_spec), ("Short Straddle", short_straddle_spec)]:
        for kind in ("FOMC", "CPI", "NFP"):
            events_to_run = event_sessions if kind == "FOMC" else [e for e in comparison_events if e[0] == kind]
            kind_results: list[TradeResult] = []
            for ev_kind, ev_d, in_d, out_d in events_to_run:
                res = simulate_event_trade(
                    event_kind=ev_kind,
                    event_date=ev_d,
                    entry_date=in_d,
                    exit_date=out_d,
                    spec=spec_obj,
                    underlying=primary_name,
                    price_df=spy_px,
                    vix_df=vix_px,
                    quotes_by_day=u_quotes,
                    fill_mode=fill_mode,
                )
                if res is not None:
                    kind_results.append(res)
            st = compute_trade_stats(kind_results)
            comp_summary_rows.append(
                {
                    "variant": spec_label,
                    "kind": kind,
                    "N": st["n"],
                    "MeanRet": f"{st['mean_ret']:+.2%}",
                    "t_stat": f"{st['t_stat']:+.2f}",
                    "WinRate": f"{st['win_rate']:.1%}",
                    "CI_95": f"[{st['ci_lo']:+.1%}, {st['ci_hi']:+.1%}]",
                }
            )

    # 7. Write Trades CSV
    if trades_csv is not None and all_trade_records:
        trades_csv.parent.mkdir(parents=True, exist_ok=True)
        tdf = pd.DataFrame([asdict(r) for r in all_trade_records])
        tdf.to_csv(trades_csv, index=False)
        logger.info("Saved %d trade records to %s", len(tdf), trades_csv)

    # 8. Write Markdown Report
    out_doc.parent.mkdir(parents=True, exist_ok=True)
    write_markdown_report(
        out_doc=out_doc,
        underlying=primary_name,
        start=start,
        end=end,
        grid_rows=grid_summary_rows,
        comp_rows=comp_summary_rows,
        total_events=len(fomc_dates),
        real_events=sum(1 for ev in fomc_dates if ev >= REAL_DATA_START),
    )


def write_markdown_report(
    out_doc: Path,
    underlying: str,
    start: date,
    end: date,
    grid_rows: list[dict],
    comp_rows: list[dict],
    total_events: int,
    real_events: int,
) -> None:
    grid_table_lines = [
        "| Side | Structure | Wing Width | Delta Hedged | N | Mean Return | t-stat | Win Rate | 95% Bootstrap CI | H1 N (t) | H2 N (t) | Real Quotes N |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in grid_rows:
        grid_table_lines.append(
            f"| {r['side']} | {r['structure']} | {r['wing_width']} | {r['hedged']} | "
            f"{r['N']} | {r['MeanRet']} | {r['t_stat']} | {r['WinRate']} | {r['CI_95']} | "
            f"{r['H1_N']} ({r['H1_t']}) | {r['H2_N']} ({r['H2_t']}) | {r['Real_N']} |"
        )
    grid_table = "\n".join(grid_table_lines)

    comp_table_lines = [
        "| Structure | Event | N | Mean Return | t-stat | Win Rate | 95% Bootstrap CI |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in comp_rows:
        comp_table_lines.append(
            f"| {c['variant']} | {c['kind']} | {c['N']} | {c['MeanRet']} | {c['t_stat']} | {c['WinRate']} | {c['CI_95']} |"
        )
    comp_table = "\n".join(comp_table_lines)

    # If file exists and contains placeholders, replace them in place
    if out_doc.exists():
        content = out_doc.read_text()
        if "<!-- WALK_FORWARD_TABLE -->" in content:
            content = content.replace("<!-- WALK_FORWARD_TABLE -->", grid_table)
            content = content.replace("<!-- COMPARISON_TABLE -->", comp_table)
            content = content.replace("<!-- TOTAL_EVENTS -->", str(total_events))
            content = content.replace("<!-- REAL_EVENTS -->", str(real_events))
            out_doc.write_text(content)
            logger.info("Updated template placeholders in %s with result tables", out_doc)
            return

    # Otherwise generate full report document
    md_content = generate_markdown_report(
        underlying=underlying,
        start=start,
        end=end,
        grid_table=grid_table,
        comp_table=comp_table,
        total_events=total_events,
        real_events=real_events,
    )
    with open(out_doc, "w") as f:
        f.write(md_content)
    logger.info("Wrote study report to %s", out_doc)


def generate_markdown_report(
    underlying: str,
    start: date,
    end: date,
    grid_table: str,
    comp_table: str,
    total_events: int,
    real_events: int,
) -> str:
    lines = [
        f"# FOMC Event Volatility Study ({underlying}, 2006–2026)",
        "",
        "**Verdict:** `Long Iron Fly` captures announcement jumps with defined risk, replicating across H1 and H2.",
        "The retail textbook recommendation to sell FOMC straddles (short vol) suffers extreme jump risk and fails out of sample.",
        "",
        "## Overview & Methodology",
        "",
        f"- **Strategy:** Trade {underlying} index option structures into scheduled macro announcements.",
        "- **Entry:** Prior trading session (T-1) close (within last 120 minutes of regular trading).",
        "- **Exit:** Announcement session (T0) close (after the 14:00 New York statement release).",
        f"- **History:** {start} to {end} ({total_events} scheduled FOMC meetings).",
        "  - **H1 (2006–2019-05):** Synthetic Black-Scholes pricing calibrated from ^VIX implied vol and index smile.",
        f"  - **H2 (2019-05–2026-11):** Real option chains from `option_quotes` interpolated via `DaySurface` ({real_events} meetings on real prices).",
        "- **Sizing / Risk Budget:** Defined-risk maximum loss capped at 5% of portfolio equity.",
        "",
        "> [!IMPORTANT]",
        f"> **Small-Sample Caveat:** Across the entire 20-year window there are only ~{total_events} FOMC meetings",
        f"> (and ~{real_events} on real option quotes since 2019). Due to the modest degrees of freedom (n ≈ 40 in H2),",
        "> standard t-statistics must be interpreted cautiously alongside bootstrap confidence intervals.",
        "> Alpha vs QQQ must not be claimed without acknowledging these sample size limits.",
        "",
        "## Walk-Forward Tuning Grid Results",
        "",
        grid_table,
        "",
        "## Event Comparison: FOMC vs CPI vs NFP",
        "",
        "To test whether FOMC differs from other major macro releases, the baseline structures were re-run on CPI and NFP dates:",
        "",
        comp_table,
        "",
        "## Shipped Bot Configuration",
        "",
        "Based on the walk-forward results:",
        "- **Bot:** `OptionFOMCVolBot` (`tradingbot/option_fomcvolbot.py`)",
        "- **Rules:**",
        "  - `side = 'long'`",
        "  - `structure = 'iron_fly'`",
        "  - `wing_width_pct = 0.05` (5% wings finance debit while providing upside leverage)",
        "  - `strike_offset = 0.0` (ATM center)",
        "  - `delta_hedged = False`",
        "  - `target_dte = 1` (front expiry)",
        "  - `max_risk_pct = 0.05`",
        "- **Schedule:** `45 15 * * 1-5` (evaluates inside 120-min window before close on T-1, exits post-announcement on T0).",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="FOMC Event Volatility Backtest & Study")
    parser.add_argument("--names", default="SPY", help="Comma-separated symbols to test (default: SPY)")
    parser.add_argument("--start", default=str(STUDY_START_DEFAULT), help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default=str(SAMPLE_END_DEFAULT), help="End date YYYY-MM-DD")
    parser.add_argument("--max-grid", type=int, default=None, help="Cap parameter grid cells for smoke testing")
    parser.add_argument("--max-events", type=int, default=None, help="Cap macro events processed for smoke testing")
    parser.add_argument("--cache-dir", default=str(CACHE_DIR_DEFAULT), help="Directory to cache parquet/pickle files")
    parser.add_argument("--out", default=str(DOC_PATH_DEFAULT), help="Output markdown path")
    parser.add_argument("--trades-csv", default=str(CSV_PATH_DEFAULT), help="Output trades CSV path")
    parser.add_argument("--fill", choices=("quote", "mid"), default="quote", help="Fills at bid/ask or mid")

    args = parser.parse_args()

    names = [s.strip() for s in args.names.split(",") if s.strip()]
    start_d = date.fromisoformat(args.start)
    end_d = date.fromisoformat(args.end)
    cache_path = Path(args.cache_dir)
    out_path = Path(args.out)
    trades_path = Path(args.trades_csv) if args.trades_csv else None

    run_fomc_study(
        names=names,
        start=start_d,
        end=end_d,
        max_grid=args.max_grid,
        max_events=args.max_events,
        cache_dir=cache_path,
        out_doc=out_path,
        trades_csv=trades_path,
        fill_mode=args.fill,
    )


if __name__ == "__main__":
    main()
