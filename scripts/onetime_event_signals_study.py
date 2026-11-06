"""
Signals Bank follow-the-signal event study (PLAN.md Step E3).

Studies Telegram channel -1001998690333 ('The Signals Bank - FREE') from
telegram_messages table. Maps symbols to Yahoo Finance tickers, parses BUY/LONG
vs SELL/SHORT directions from AI summaries using a keyword rule, and evaluates:
1. Per-event signed forward returns (raw and abnormal vs QQQ at +1, +3, +5 sessions).
2. Equal-weight strategy daily returns and alpha stats vs QQQ across pre-registered
   holding period and direction variants using tradingbot.utils.event_study.

Pre-registered Variant Grid:
----------------------------
- hold_days: [1, 3, 5] sessions
- subsets:
    * all_signed: both long (+1) and short (-1) signals
    * long_only: BUY / LONG signals only (direction == 1, executable by framework)
    * short_only: SELL / SHORT signals only (direction == -1, theoretical / benchmark)
Total pre-registered variants: 9 (3 holding periods x 3 direction subsets).
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass

import dotenv
import numpy as np
import pandas as pd
import yfinance as yf

# DB access requirement: load .env.secret if POSTGRES_URI is unset
if not os.environ.get("POSTGRES_URI"):
    dotenv.load_dotenv(".env.secret")

from tradingbot.utils.db import TelegramMessage, get_db_session
from tradingbot.utils.event_study import abnormal_returns, event_strategy_returns, judge

CHANNEL_ID = "-1001998690333"

# Pre-registered variant grid
PRE_REGISTERED_VARIANTS: list[dict[str, str | int]] = [
    {"name": "all_signed_h1", "subset": "all_signed", "hold_days": 1},
    {"name": "all_signed_h3", "subset": "all_signed", "hold_days": 3},
    {"name": "all_signed_h5", "subset": "all_signed", "hold_days": 5},
    {"name": "long_only_h1", "subset": "long_only", "hold_days": 1},
    {"name": "long_only_h3", "subset": "long_only", "hold_days": 3},
    {"name": "long_only_h5", "subset": "long_only", "hold_days": 5},
    {"name": "short_only_h1", "subset": "short_only", "hold_days": 1},
    {"name": "short_only_h3", "subset": "short_only", "hold_days": 3},
    {"name": "short_only_h5", "subset": "short_only", "hold_days": 5},
]

# Static mapping dictionary for symbols appearing in channel -1001998690333
SYMBOL_MAP: dict[str, str] = {
    # Metals / Commodities
    "XAUUSD": "GC=F",
    "XAU": "GC=F",
    "XAU/USD": "GC=F",
    "GOLD": "GC=F",
    "GC": "GC=F",
    "XAGUSD": "SI=F",
    "XAG": "SI=F",
    "WTI": "CL=F",
    "CL": "CL=F",
    "CL=F": "CL=F",
    # US Indices
    "US500": "^GSPC",
    "US500.C": "^GSPC",
    "US500.c": "^GSPC",
    "SP500": "^GSPC",
    "SPX": "^GSPC",
    "SPX500": "^GSPC",
    "US30": "^DJI",
    "US30.c": "^DJI",
    "DJ30": "^DJI",
    "DJ30.c": "^DJI",
    "DJI": "^DJI",
    "DJIA": "^DJI",
    "WS30": "^DJI",
    "NAS100": "^NDX",
    "NDX": "^NDX",
    "US100": "^NDX",
    "USTEC": "^NDX",
    "USTEC.C": "^NDX",
    "USTEC.c": "^NDX",
    # European / Global Indices
    "DAX": "^GDAXI",
    "DE40": "^GDAXI",
    "DE40.c": "^GDAXI",
    "HK50": "^HSI",
    "DXY": "DX-Y.NYB",
    "EXY": "DX-Y.NYB",
    "STOXX": "^STOXX50E",
    "XJO": "^AXJO",
    # Major & Minor FX pairs (Yahoo Finance ticker ends with =X)
    "EURUSD": "EURUSD=X",
    "EUR/USD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "USD/JPY": "USDJPY=X",
    "AUDUSD": "AUDUSD=X",
    "USDCAD": "USDCAD=X",
    "USD/CAD": "USDCAD=X",
    "USDCHF": "USDCHF=X",
    "NZDUSD": "NZDUSD=X",
    "NZD/USD": "NZDUSD=X",
    "AUDCAD": "AUDCAD=X",
    "AUDCHF": "AUDCHF=X",
    "AUDJPY": "AUDJPY=X",
    "AUDNZD": "AUDNZD=X",
    "CADCHF": "CADCHF=X",
    "CADHCHF": "CADCHF=X",
    "CADJPY": "CADJPY=X",
    "CHFJPY": "CHFJPY=X",
    "EURAUD": "EURAUD=X",
    "EURCAD": "EURCAD=X",
    "EURCHF": "EURCHF=X",
    "EURGBP": "EURGBP=X",
    "EURJPY": "EURJPY=X",
    "EURNZD": "EURNZD=X",
    "GBPCAD": "GBPCAD=X",
    "GBPCHF": "GBPCHF=X",
    "GBPNZD": "GBPNZD=X",
    "NZDCAD": "NZDCAD=X",
    "NZDCHF": "NZDCHF=X",
    "USDCNY": "USDCNY=X",
    "USD/CNY": "USDCNY=X",
    "USD/KRW": "USDKRW=X",
    "USD/MXN": "USDMXN=X",
    "USDINR": "USDINR=X",
    # Crypto
    "BTC": "BTC-USD",
    "BTCUSD": "BTC-USD",
    "BTC/USD": "BTC-USD",
    "BTC/USDT": "BTC-USD",
    "ETH": "ETH-USD",
    "ETHUSD": "ETH-USD",
    "ETH/USD": "ETH-USD",
    "SOL": "SOL-USD",
    "XRP": "XRP-USD",
    "ADA": "ADA-USD",
    "BNB": "BNB-USD",
    "AAVE": "AAVE-USD",
    "EGLD": "EGLD-USD",
    "LINK": "LINK-USD",
    "SHIB": "SHIB-USD",
    "TAO": "TAO-USD",
    "XLM": "XLM-USD",
    "ZEC": "ZEC-USD",
    # Equities
    "AAPL": "AAPL",
    "AXP": "AXP",
    "FOXA": "FOXA",
    "GOOGL": "GOOGL",
    "MSTR": "MSTR",
    "SOFI": "SOFI",
    "TSLA": "TSLA",
}


def map_symbol(raw: str | None) -> str | None:
    """
    Map channel symbol text to Yahoo Finance ticker.
    Unmappable or unknown symbols return None (dropped, not defaulted).
    """
    if not raw:
        return None
    sym = raw.strip()
    # Check exact match, uppercase match, and cleaned-slash match
    if sym in SYMBOL_MAP:
        return SYMBOL_MAP[sym]
    upper = sym.upper()
    if upper in SYMBOL_MAP:
        return SYMBOL_MAP[upper]
    return None


def parse_direction(summary: str | None) -> int | None:
    """
    Direction from summary text via keyword rule (BUY/LONG vs SELL/SHORT).
    Returns +1 for BUY/LONG, -1 for SELL/SHORT, or None if ambiguous/neither.
    """
    if not summary:
        return None

    # Remove phrases like 'short-term', 'short term', 'short‑term'
    # so temporal descriptions don't trigger short-position signals.
    cleaned = re.sub(r"\bshort[\-‑\s]term\b", "", summary, flags=re.IGNORECASE)

    has_buy = bool(re.search(r"\b(BUY|LONG)\b", cleaned, re.IGNORECASE))
    has_sell = bool(re.search(r"\b(SELL|SHORT)\b", cleaned, re.IGNORECASE))

    if has_buy and not has_sell:
        return 1
    elif has_sell and not has_buy:
        return -1
    return None


def load_signals_from_db(channel: str = CHANNEL_ID) -> pd.DataFrame:
    """
    Load signals from database for the specified channel, extracting plain Python types
    to avoid DetachedInstanceError.
    """
    with get_db_session() as session:
        msgs = (
            session.query(
                TelegramMessage.id,
                TelegramMessage.published_at,
                TelegramMessage.symbol,
                TelegramMessage.summary,
            )
            .filter(
                TelegramMessage.channel == channel,
                TelegramMessage.symbol.isnot(None),
                TelegramMessage.summary.isnot(None),
            )
            .order_by(TelegramMessage.published_at.asc())
            .all()
        )
        raw_rows = [(m[0], m[1], m[2], m[3]) for m in msgs]

    records = []
    unmapped_count = 0
    total_rows = len(raw_rows)

    for db_id, pub_at, raw_sym, summary in raw_rows:
        mapped_sym = map_symbol(raw_sym)
        if mapped_sym is None:
            unmapped_count += 1
            continue

        direction = parse_direction(summary)
        records.append(
            {
                "id": db_id,
                "ts": pub_at,
                "raw_symbol": raw_sym,
                "symbol": mapped_sym,
                "summary": summary,
                "direction": direction,
            }
        )

    df = pd.DataFrame(records)
    print(f"Total rows queried: {total_rows}")
    print(f"Successfully mapped to yfinance ticker: {len(df)} ({len(df)/total_rows:.1%})")
    print(f"Unmapped / dropped rows: {unmapped_count}")
    return df


def run_signals_study() -> dict:
    """
    Run the full E3 study and return all computed statistics and tables.
    """
    df_all = load_signals_from_db(CHANNEL_ID)

    # Filter to rows that have a parsed direction
    df_signals = df_all[df_all["direction"].notna()].copy()
    df_signals["direction"] = df_signals["direction"].astype(int)

    n_total = len(df_all)
    n_mapped = len(df_signals)
    n_buys = int((df_signals["direction"] == 1).sum())
    n_sells = int((df_signals["direction"] == -1).sum())
    n_no_dir = n_total - n_mapped

    print("\nDirection breakdown of mapped signals:")
    print(f"  BUY / LONG:  {n_buys}")
    print(f"  SELL / SHORT: {n_sells}")
    print(f"  Ambiguous / No signal keyword: {n_no_dir}")

    min_ts = df_signals["ts"].min()
    max_ts = df_signals["ts"].max()
    print(f"\nSignal date range: {min_ts} to {max_ts}")

    # Unique tickers needed + benchmark
    unique_tickers = sorted(df_signals["symbol"].unique().tolist() + ["QQQ"])
    print(f"Downloading daily price history for {len(unique_tickers)} tickers...")
    px_df = yf.download(unique_tickers, start="2025-01-01", auto_adjust=True, progress=False)["Close"]

    prices: dict[str, pd.Series] = {t: px_df[t].dropna() for t in unique_tickers if t in px_df.columns}
    bench_prices = prices["QQQ"]
    bench_returns = bench_prices.pct_change().dropna()

    # 1. Per-event forward returns and abnormal returns at horizons [1, 3, 5]
    print("\nComputing per-event forward and abnormal returns...")
    events_input = df_signals[["ts", "symbol", "direction"]].copy()
    ar_df = abnormal_returns(events_input, prices, bench_prices, horizons=[1, 3, 5])
    ar_df["signed_raw"] = ar_df["raw"] * ar_df["direction"]
    ar_df["signed_abnormal"] = ar_df["abnormal"] * ar_df["direction"]

    fwd_stats_all = ar_df.groupby("horizon")[["raw", "signed_raw", "abnormal", "signed_abnormal"]].mean()
    fwd_stats_long = ar_df[ar_df["direction"] == 1].groupby("horizon")[["raw", "signed_raw", "abnormal", "signed_abnormal"]].mean()
    fwd_stats_short = ar_df[ar_df["direction"] == -1].groupby("horizon")[["raw", "signed_raw", "abnormal", "signed_abnormal"]].mean()

    # 2. Portfolio Strategy backtest across pre-registered variants
    # Slice the evaluation window to start at the first event's date
    start_dt = pd.Timestamp(min_ts.date())
    variant_results = []

    print("\nEvaluating Pre-registered Strategy Variants:")
    print("-" * 105)
    print(f"{'Variant':<16} | {'Alpha':<8} | {'t-stat':<7} | {'Beta':<7} | {'Corr':<6} | {'n':<5} | {'Alpha H1':<9} | {'Alpha H2':<9} | {'Passes':<6}")
    print("-" * 105)

    for var in PRE_REGISTERED_VARIANTS:
        name = str(var["name"])
        subset_kind = str(var["subset"])
        hold = int(var["hold_days"])

        if subset_kind == "all_signed":
            subset_df = df_signals
        elif subset_kind == "long_only":
            subset_df = df_signals[df_signals["direction"] == 1]
        elif subset_kind == "short_only":
            subset_df = df_signals[df_signals["direction"] == -1]
        else:
            raise ValueError(f"Unknown subset {subset_kind}")

        strat_rets = event_strategy_returns(subset_df, prices, hold_days=hold)
        s_sliced = strat_rets[strat_rets.index >= start_dt]
        b_sliced = bench_returns[bench_returns.index >= start_dt]

        judgement = judge(s_sliced, b_sliced)
        var_res = {
            "variant": name,
            "subset": subset_kind,
            "hold_days": hold,
            "alpha": judgement["alpha"],
            "t": judgement["t"],
            "beta": judgement["beta"],
            "corr": judgement["corr"],
            "n": judgement["n"],
            "alpha_h1": judgement["alpha_h1"],
            "t_h1": judgement["t_h1"],
            "alpha_h2": judgement["alpha_h2"],
            "t_h2": judgement["t_h2"],
            "passes": judgement["passes"],
        }
        variant_results.append(var_res)

        alpha_str = f"{var_res['alpha']:.4f}" if var_res['alpha'] is not None else "None"
        t_str = f"{var_res['t']:.2f}" if var_res['t'] is not None else "None"
        beta_str = f"{var_res['beta']:.4f}" if var_res['beta'] is not None else "None"
        corr_str = f"{var_res['corr']:.4f}" if var_res['corr'] is not None else "None"
        n_str = str(var_res["n"])
        h1_str = f"{var_res['alpha_h1']:.4f}" if var_res['alpha_h1'] is not None else "None"
        h2_str = f"{var_res['alpha_h2']:.4f}" if var_res['alpha_h2'] is not None else "None"
        pass_str = "YES" if var_res["passes"] else "NO"

        print(f"{name:<16} | {alpha_str:<8} | {t_str:<7} | {beta_str:<7} | {corr_str:<6} | {n_str:<5} | {h1_str:<9} | {h2_str:<9} | {pass_str:<6}")

    print("-" * 105)

    return {
        "n_total": n_total,
        "n_mapped": n_mapped,
        "n_buys": n_buys,
        "n_sells": n_sells,
        "n_no_dir": n_no_dir,
        "min_ts": min_ts,
        "max_ts": max_ts,
        "fwd_stats_all": fwd_stats_all,
        "fwd_stats_long": fwd_stats_long,
        "fwd_stats_short": fwd_stats_short,
        "variant_results": variant_results,
    }


if __name__ == "__main__":
    run_signals_study()
