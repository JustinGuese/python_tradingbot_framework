"""
Onetime Event Study: Stock News Headline Drift (Step E2).

Investigates whether sentiment from yfinance news headlines in `stock_news`
predicts post-announcement abnormal returns and generates alpha vs QQQ.

Pre-registered Variant Grid:
----------------------------
1. Event-level Abnormal Returns (horizons = +1, +3, +5 sessions):
   - Positive sentiment (score > 0)
   - Negative sentiment (score < 0) - short/avoid thesis
   - Neutral sentiment (score == 0)
   - Top tercile sentiment
   - Bottom tercile sentiment
   - Top - Bottom spread
   Each evaluated across: Full sample, H1 (first half by date), H2 (second half by date).
   Any bucket with N < 30 is flagged "too few".

2. Portfolio Strategy Variants (evaluated vs QQQ via judge / alpha_stats):
   - long_positive_h1: Buy positive sentiment (score > 0), hold 1 session
   - long_positive_h3: Buy positive sentiment (score > 0), hold 3 sessions
   - long_positive_h5: Buy positive sentiment (score > 0), hold 5 sessions
   - top_tercile_h1: Buy top tercile sentiment, hold 1 session
   - top_tercile_h3: Buy top tercile sentiment, hold 3 sessions
   - top_tercile_h5: Buy top tercile sentiment, hold 5 sessions
   - short_negative_h1: Short negative sentiment (score < 0), hold 1 session
   - short_negative_h3: Short negative sentiment (score < 0), hold 3 sessions
   - short_negative_h5: Short negative sentiment (score < 0), hold 5 sessions
   - bottom_tercile_h1: Short bottom tercile sentiment, hold 1 session
   - bottom_tercile_h3: Short bottom tercile sentiment, hold 3 sessions
   - bottom_tercile_h5: Short bottom tercile sentiment, hold 5 sessions

Acceptance / Passing criteria (CLAUDE.md / PLAN.md):
   t >= 2.0, alpha > 0 in BOTH halves (H1 and H2), and beta < 0.5.
"""

from __future__ import annotations

import os
import re
import warnings
from collections.abc import Sequence
from datetime import UTC

import dotenv

if not os.environ.get("POSTGRES_URI"):
    dotenv.load_dotenv(".env.secret")

import numpy as np
import pandas as pd
import yfinance as yf

from tradingbot.utils.db import StockNews, get_db_session
from tradingbot.utils.event_study import (
    abnormal_returns,
    event_strategy_returns,
    judge,
)

warnings.filterwarnings("ignore")

# Finance sentiment lexicon (positive / negative word lists, deterministic, no LLM)
POSITIVE_WORDS = {
    "advance",
    "advances",
    "advanced",
    "advancing",
    "beat",
    "beats",
    "beating",
    "boom",
    "booming",
    "boost",
    "boosts",
    "boosted",
    "boosting",
    "breakout",
    "bullish",
    "buy",
    "buys",
    "buying",
    "buyback",
    "buybacks",
    "climb",
    "climbs",
    "climbed",
    "climbing",
    "crush",
    "crushes",
    "crushed",
    "crushing",
    "exceed",
    "exceeds",
    "exceeded",
    "exceeding",
    "gain",
    "gains",
    "gained",
    "gaining",
    "growth",
    "high",
    "higher",
    "highest",
    "jump",
    "jumps",
    "jumped",
    "jumping",
    "optimism",
    "optimistic",
    "outperform",
    "outperformed",
    "outperforms",
    "outperforming",
    "profit",
    "profitable",
    "profitability",
    "rally",
    "rallies",
    "rallied",
    "rallying",
    "rebound",
    "rebounds",
    "rebounded",
    "rebounding",
    "record",
    "recovery",
    "rise",
    "rises",
    "rising",
    "rose",
    "soar",
    "soars",
    "soared",
    "soaring",
    "strength",
    "strong",
    "stronger",
    "strongest",
    "surge",
    "surges",
    "surged",
    "surging",
    "surpass",
    "surpasses",
    "surpassed",
    "surpassing",
    "top",
    "tops",
    "topped",
    "topping",
    "upgrade",
    "upgrades",
    "upgraded",
    "upgrading",
    "upside",
    "winner",
    "winners",
    "winning",
}

NEGATIVE_WORDS = {
    "bearish",
    "crash",
    "crashes",
    "crashed",
    "crashing",
    "cut",
    "cuts",
    "cutting",
    "decline",
    "declines",
    "declined",
    "declining",
    "default",
    "defaults",
    "deficit",
    "downgrade",
    "downgrades",
    "downgraded",
    "downgrading",
    "downside",
    "drop",
    "drops",
    "dropped",
    "dropping",
    "fail",
    "fails",
    "failed",
    "failing",
    "failure",
    "fall",
    "falls",
    "fallen",
    "falling",
    "fell",
    "fraud",
    "investigation",
    "investigations",
    "lawsuit",
    "lawsuits",
    "layoff",
    "layoffs",
    "loss",
    "losses",
    "lost",
    "losing",
    "low",
    "lower",
    "lowest",
    "miss",
    "misses",
    "missed",
    "missing",
    "pessimism",
    "pessimistic",
    "plunge",
    "plunges",
    "plunged",
    "plunging",
    "probe",
    "probes",
    "probed",
    "risk",
    "risks",
    "risky",
    "scandal",
    "scandals",
    "sink",
    "sinks",
    "sank",
    "sunk",
    "sinking",
    "slump",
    "slumps",
    "slumped",
    "slumping",
    "struggle",
    "struggles",
    "struggled",
    "struggling",
    "sue",
    "sued",
    "sues",
    "suing",
    "tumble",
    "tumbles",
    "tumbled",
    "tumbling",
    "underperform",
    "underperformed",
    "underperforms",
    "underperforming",
    "warn",
    "warns",
    "warned",
    "warning",
    "warnings",
    "weak",
    "weaker",
    "weakest",
    "weakness",
}

NEGATIONS = {
    "not",
    "no",
    "never",
    "without",
    "hardly",
    "barely",
    "unable",
    "neither",
    "nor",
    "cannot",
    "cant",
    "can't",
    "dont",
    "don't",
    "doesnt",
    "doesn't",
    "didnt",
    "didn't",
    "wont",
    "won't",
    "isnt",
    "isn't",
    "arent",
    "aren't",
    "fail",
    "fails",
    "failed",
    "failing",
}

# Pre-registered variants
PORTFOLIO_VARIANTS = [
    ("long_positive_h1", "positive", 1, 1.0),
    ("long_positive_h3", "positive", 3, 1.0),
    ("long_positive_h5", "positive", 5, 1.0),
    ("top_tercile_h1", "top_tercile", 1, 1.0),
    ("top_tercile_h3", "top_tercile", 3, 1.0),
    ("top_tercile_h5", "top_tercile", 5, 1.0),
    ("short_negative_h1", "negative", 1, -1.0),
    ("short_negative_h3", "negative", 3, -1.0),
    ("short_negative_h5", "negative", 5, -1.0),
    ("bottom_tercile_h1", "bottom_tercile", 1, -1.0),
    ("bottom_tercile_h3", "bottom_tercile", 3, -1.0),
    ("bottom_tercile_h5", "bottom_tercile", 5, -1.0),
]


def score_headline(title: str) -> int:
    """
    Score headline sentiment using finance lexicon with negation handling.

    Returns integer score: positive (>0), negative (<0), or neutral (0).
    A negation word within a 3-token lookback window flips the polarity.
    """
    if not title or not isinstance(title, str):
        return 0

    clauses = re.split(r"[,.;:!?]", title)
    total_score = 0

    for clause in clauses:
        tokens = re.findall(r"[a-zA-Z']+", clause.lower())
        for i, tok in enumerate(tokens):
            val = 0
            if tok in POSITIVE_WORDS:
                val = 1
            elif tok in NEGATIVE_WORDS:
                val = -1

            if val != 0:
                lookback = tokens[max(0, i - 3) : i]
                if any(w in NEGATIONS for w in lookback):
                    val = -val
                total_score += val

    return total_score


def dedupe_headlines(df: pd.DataFrame) -> pd.DataFrame:
    """
    Dedupe identical titles per symbol per day.
    Keeps the first occurrence.
    """
    if df is None or df.empty:
        return pd.DataFrame() if df is None else df.copy()

    out = df.copy()
    ts_col = "published_at" if "published_at" in out.columns else ("ts" if "ts" in out.columns else None)
    if ts_col is not None:
        dates = pd.to_datetime(out[ts_col]).dt.date
    else:
        dates = pd.Series(["1970-01-01"] * len(out), index=out.index)

    clean_titles = out["title"].astype(str).str.strip().str.lower()
    mask = ~pd.Series(list(zip(out["symbol"], dates, clean_titles, strict=False)), index=out.index).duplicated(
        keep="first"
    )
    return out[mask].copy()


def assign_terciles(df: pd.DataFrame) -> pd.DataFrame:
    """
    Assign tercile labels (bottom, middle, top) based on sentiment score.
    """
    out = df.copy()
    if len(out) < 3:
        out["tercile"] = "middle"
        return out

    # Deterministic ranking: rank by score, then by index to break ties
    ranks = out["score"].rank(method="first")
    out["tercile"] = pd.qcut(ranks, 3, labels=["bottom", "middle", "top"])
    return out


def load_news_from_db() -> pd.DataFrame:
    """Fetch all rows from stock_news table."""
    with get_db_session() as session:
        rows = (
            session.query(
                StockNews.symbol,
                StockNews.title,
                StockNews.published_at,
            )
            .order_by(StockNews.published_at.asc())
            .all()
        )

    df = pd.DataFrame(rows, columns=["symbol", "title", "published_at"])
    # Ensure UTC timezone awareness
    df["ts"] = pd.to_datetime(df["published_at"])
    if df["ts"].dt.tz is None:
        df["ts"] = df["ts"].dt.tz_localize(UTC)
    else:
        df["ts"] = df["ts"].dt.tz_convert(UTC)

    return df


def load_prices(symbols: Sequence[str], start_date: str = "2023-01-01") -> tuple[dict[str, pd.Series], pd.Series]:
    """
    Load daily adjusted close prices for symbols and benchmark QQQ.
    Uses cached pickle file so subsequent reruns are fast.
    """
    cache_path = os.environ.get("EVENT_NEWS_PRICES_CACHE", ".cache/event_news_prices.pkl")
    all_symbols = sorted(set(symbols) | {"QQQ"})

    if os.path.exists(cache_path):
        try:
            cached_df = pd.read_pickle(cache_path)
            if (
                isinstance(cached_df, pd.DataFrame)
                and set(symbols).issubset(set(cached_df.columns))
                and "QQQ" in cached_df.columns
            ):
                prices = {s: cached_df[s].dropna() for s in symbols if s in cached_df.columns}
                bench = cached_df["QQQ"].dropna()
                return prices, bench
        except Exception:
            pass

    print(f"Downloading daily prices for {len(all_symbols)} tickers from Yahoo Finance...")
    data = yf.download(all_symbols, start=start_date, auto_adjust=True, progress=False)
    close_df = data["Close"]

    try:
        os.makedirs(os.path.dirname(os.path.abspath(cache_path)), exist_ok=True)
        close_df.to_pickle(cache_path)
    except Exception:
        pass

    prices = {s: close_df[s].dropna() for s in symbols if s in close_df.columns}
    bench = close_df["QQQ"].dropna()
    return prices, bench


def run_event_news_study() -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """
    Execute step E2 event news study.
    Returns:
      (abnormal_results_summary_df, portfolio_results_df, verdict_string)
    """
    # 1. Load data
    raw_news = load_news_from_db()

    # 2. Dedupe
    deduped = dedupe_headlines(raw_news)

    # 3. Score
    deduped["score"] = deduped["title"].apply(score_headline)
    deduped = assign_terciles(deduped)

    # Buckets
    deduped["bucket"] = "neutral"
    deduped.loc[deduped["score"] > 0, "bucket"] = "positive"
    deduped.loc[deduped["score"] < 0, "bucket"] = "negative"

    # 4. Load prices
    unique_symbols = sorted(deduped["symbol"].unique())
    prices, bench = load_prices(unique_symbols)

    # 5. Event-level abnormal returns across horizons [1, 3, 5]
    horizons = [1, 3, 5]
    abn_df = abnormal_returns(deduped, prices, bench, horizons)

    # Merge bucket and tercile info
    # abn_df has original columns from deduped
    # Split sample in half by event date
    deduped_sorted = deduped.sort_values("ts")
    mid_ts = deduped_sorted.iloc[len(deduped_sorted) // 2]["ts"]

    abn_df["sample"] = "full"
    # We can evaluate full, H1 (ts < mid_ts), and H2 (ts >= mid_ts)
    abn_df["is_h1"] = abn_df["ts"] < mid_ts

    # Summarize abnormal returns per bucket and horizon
    summary_rows = []
    bucket_definitions = [
        ("positive", abn_df[abn_df["bucket"] == "positive"]),
        ("negative", abn_df[abn_df["bucket"] == "negative"]),
        ("neutral", abn_df[abn_df["bucket"] == "neutral"]),
        ("top_tercile", abn_df[abn_df["tercile"] == "top"]),
        ("bottom_tercile", abn_df[abn_df["tercile"] == "bottom"]),
    ]

    for bucket_name, b_df in bucket_definitions:
        for h in horizons:
            sub_full = b_df[b_df["horizon"] == h]["abnormal"].dropna()
            sub_h1 = b_df[(b_df["horizon"] == h) & (b_df["is_h1"])]["abnormal"].dropna()
            sub_h2 = b_df[(b_df["horizon"] == h) & (~b_df["is_h1"])]["abnormal"].dropna()

            n_full = len(sub_full)
            mean_full = float(sub_full.mean()) if n_full > 0 else np.nan
            std_full = float(sub_full.std()) if n_full > 1 else np.nan
            t_full = (mean_full / (std_full / np.sqrt(n_full))) if (n_full > 1 and std_full > 0) else 0.0

            n_h1 = len(sub_h1)
            mean_h1 = float(sub_h1.mean()) if n_h1 > 0 else np.nan
            std_h1 = float(sub_h1.std()) if n_h1 > 1 else np.nan
            t_h1 = (mean_h1 / (std_h1 / np.sqrt(n_h1))) if (n_h1 > 1 and std_h1 > 0) else 0.0

            n_h2 = len(sub_h2)
            mean_h2 = float(sub_h2.mean()) if n_h2 > 0 else np.nan
            std_h2 = float(sub_h2.std()) if n_h2 > 1 else np.nan
            t_h2 = (mean_h2 / (std_h2 / np.sqrt(n_h2))) if (n_h2 > 1 and std_h2 > 0) else 0.0

            flag = "too few" if n_full < 30 else "ok"

            summary_rows.append(
                {
                    "bucket": bucket_name,
                    "horizon": f"+{h}d",
                    "n": n_full,
                    "mean_abn_pct": mean_full * 100.0,
                    "t_stat": t_full,
                    "h1_mean_pct": mean_h1 * 100.0,
                    "h1_t": t_h1,
                    "h2_mean_pct": mean_h2 * 100.0,
                    "h2_t": t_h2,
                    "flag": flag,
                }
            )

    # Add Top - Bottom tercile spread
    for h in horizons:
        top_h = abn_df[(abn_df["tercile"] == "top") & (abn_df["horizon"] == h)]["abnormal"].dropna()
        bot_h = abn_df[(abn_df["tercile"] == "bottom") & (abn_df["horizon"] == h)]["abnormal"].dropna()

        diff_mean = (top_h.mean() - bot_h.mean()) * 100.0
        # Welch's t-test
        se_top = top_h.std() / np.sqrt(len(top_h)) if len(top_h) > 1 else 0
        se_bot = bot_h.std() / np.sqrt(len(bot_h)) if len(bot_h) > 1 else 0
        se_diff = np.sqrt(se_top**2 + se_bot**2)
        t_diff = (top_h.mean() - bot_h.mean()) / se_diff if se_diff > 0 else 0.0

        top_h1 = abn_df[(abn_df["tercile"] == "top") & (abn_df["horizon"] == h) & (abn_df["is_h1"])][
            "abnormal"
        ].dropna()
        bot_h1 = abn_df[(abn_df["tercile"] == "bottom") & (abn_df["horizon"] == h) & (abn_df["is_h1"])][
            "abnormal"
        ].dropna()
        diff_h1 = (top_h1.mean() - bot_h1.mean()) * 100.0
        se_top_h1 = top_h1.std() / np.sqrt(len(top_h1)) if len(top_h1) > 1 else 0
        se_bot_h1 = bot_h1.std() / np.sqrt(len(bot_h1)) if len(bot_h1) > 1 else 0
        se_diff_h1 = np.sqrt(se_top_h1**2 + se_bot_h1**2)
        t_h1 = (top_h1.mean() - bot_h1.mean()) / se_diff_h1 if se_diff_h1 > 0 else 0.0

        top_h2 = abn_df[(abn_df["tercile"] == "top") & (abn_df["horizon"] == h) & (~abn_df["is_h1"])][
            "abnormal"
        ].dropna()
        bot_h2 = abn_df[(abn_df["tercile"] == "bottom") & (abn_df["horizon"] == h) & (~abn_df["is_h1"])][
            "abnormal"
        ].dropna()
        diff_h2 = (top_h2.mean() - bot_h2.mean()) * 100.0
        se_top_h2 = top_h2.std() / np.sqrt(len(top_h2)) if len(top_h2) > 1 else 0
        se_bot_h2 = bot_h2.std() / np.sqrt(len(bot_h2)) if len(bot_h2) > 1 else 0
        se_diff_h2 = np.sqrt(se_top_h2**2 + se_bot_h2**2)
        t_h2 = (top_h2.mean() - bot_h2.mean()) / se_diff_h2 if se_diff_h2 > 0 else 0.0

        summary_rows.append(
            {
                "bucket": "top_minus_bottom",
                "horizon": f"+{h}d",
                "n": min(len(top_h), len(bot_h)),
                "mean_abn_pct": diff_mean,
                "t_stat": t_diff,
                "h1_mean_pct": diff_h1,
                "h1_t": t_h1,
                "h2_mean_pct": diff_h2,
                "h2_t": t_h2,
                "flag": "ok",
            }
        )

    abn_summary_df = pd.DataFrame(summary_rows)

    # 6. Portfolio Strategy Variants (judged vs QQQ via judge)
    bench_returns = bench.pct_change().dropna()
    portfolio_rows = []

    for var_name, subset_type, hold_days, direction in PORTFOLIO_VARIANTS:
        if subset_type == "positive":
            sigs = deduped[deduped["bucket"] == "positive"].copy()
        elif subset_type == "negative":
            sigs = deduped[deduped["bucket"] == "negative"].copy()
        elif subset_type == "top_tercile":
            sigs = deduped[deduped["tercile"] == "top"].copy()
        elif subset_type == "bottom_tercile":
            sigs = deduped[deduped["tercile"] == "bottom"].copy()
        else:
            sigs = deduped.copy()

        sigs["direction"] = direction
        strat_returns = event_strategy_returns(sigs, prices, hold_days=hold_days)
        j = judge(strat_returns, bench_returns)

        portfolio_rows.append(
            {
                "variant": var_name,
                "hold_days": hold_days,
                "direction": "long" if direction > 0 else "short",
                "passes": j["passes"],
                "alpha": j["alpha"],
                "t": j["t"],
                "beta": j["beta"],
                "n": j["n"],
                "h1_alpha": j["h1_alpha"],
                "h1_t": j["h1_t"],
                "h2_alpha": j["h2_alpha"],
                "h2_t": j["h2_t"],
            }
        )

    portfolio_df = pd.DataFrame(portfolio_rows)

    # 7. Final verdict
    any_pass = any(portfolio_df["passes"])
    # Also check if any abnormal return t >= 2 with positive in both halves
    any_abn_edge = any(
        row["t_stat"] >= 2.0 and row["h1_mean_pct"] > 0 and row["h2_mean_pct"] > 0
        for _, row in abn_summary_df[
            abn_summary_df["bucket"].isin(["positive", "top_tercile", "top_minus_bottom"])
        ].iterrows()
    )

    if any_pass:
        verdict = "edge"
    elif any_abn_edge:
        verdict = "unproven"
    else:
        verdict = "no edge"

    return abn_summary_df, portfolio_df, verdict


def main() -> None:
    print("=" * 80)
    print("STARTING ONETIME EVENT NEWS STUDY (STEP E2)")
    print("=" * 80)

    abn_summary_df, portfolio_df, verdict = run_event_news_study()

    print("\n--- EVENT-LEVEL ABNORMAL RETURNS (%) ---")
    print(abn_summary_df.to_string(index=False))

    print("\n--- PORTFOLIO STRATEGY VARIANTS (vs QQQ) ---")
    print(portfolio_df.to_string(index=False))

    print(f"\nFinal Verdict: {verdict}")


if __name__ == "__main__":
    main()
