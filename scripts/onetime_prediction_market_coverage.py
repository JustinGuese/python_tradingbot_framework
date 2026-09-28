"""Kill test A: is there enough continuous prediction-market history to backtest an overlay?

Reads `prediction_market_snapshots` (fill it first with
`python -m tradingbot.predictionmarketsnapshot --backfill`), builds the
point-in-time feature frame on business days, and prints per feature: first
available bar, share of bars covered since then, the longest gap, and the
liquidity of the underlying rows (median open interest, bid/ask spread).

Pass criterion (docs/backtests/prediction-markets-2026-09.md): the features an
overlay would use must be continuous from about mid-2021, so a walk-forward
split at 2024-01-01 leaves a first half long enough to tune on.

Usage:
    kubectl port-forward -n tradingbots-2025 svc/psql-service 5432:5432
    POSTGRES_URI=... PYTHONPATH=. uv run python scripts/onetime_prediction_market_coverage.py
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tradingbot.utils.db import init_db
from tradingbot.utils.prediction_market_features import as_of_bars, daily_features, load_snapshots


def main() -> int:
    init_db()
    snaps = load_snapshots()
    if snaps.empty:
        print("prediction_market_snapshots is empty — run the backfill first")
        return 1
    print("## Rows per series")
    liq = (
        snaps.assign(spread=snaps["ask"] - snaps["bid"])
        .groupby("series")
        .agg(
            rows=("prob", "size"),
            markets=("market_ticker", "nunique"),
            first=("date", "min"),
            last=("date", "max"),
            median_oi=("open_interest", "median"),
            median_spread=("spread", "median"),
        )
    )
    print(liq.to_string())

    bars = pd.bdate_range("2021-06-01", pd.Timestamp.today().normalize())
    feats = as_of_bars(daily_features(snaps), bars)
    rows = []
    for col in feats.columns:
        s = feats[col]
        valid = s.notna()
        if not valid.any():
            continue
        first = s.index[valid.argmax()]
        after = valid[first:]
        runs = (~after).astype(int).groupby(after.cumsum()).sum()
        rows.append(
            {
                "feature": col,
                "first_bar": first.date(),
                "coverage_since_first": round(after.mean(), 3),
                "longest_gap_bdays": int(runs.max()),
                "mean": round(s.mean(), 3),
                "std": round(s.std(), 3),
            }
        )
    print("\n## Features on business days since 2021-06-01")
    print(pd.DataFrame(rows).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
