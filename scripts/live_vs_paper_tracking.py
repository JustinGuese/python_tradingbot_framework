#!/usr/bin/env python3
"""
Live-vs-paper tracking CLI.

Step S1c of PLAN.md:
From `live_equity` per broker and the paper blend's `portfolio_worth`
(weekday + gap rules from AGENTS.md), compute:
  - daily tracking error
  - cumulative drift
  - beta of live to paper
  - share of days the broker was flat while the blend was invested

Prints a table summarizing live execution fidelity vs the paper backtest.

Usage:
    .venv/bin/python scripts/live_vs_paper_tracking.py
    .venv/bin/python scripts/live_vs_paper_tracking.py --broker hyperliquid
    .venv/bin/python scripts/live_vs_paper_tracking.py --include-testnet
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradingbot.utils.db import get_db_session, init_db
from tradingbot.utils.live_tracking import (
    format_tracking_table,
    track_all_brokers,
    track_broker,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compute live-vs-paper tracking error, drift, beta, and flat-broker share."
    )
    parser.add_argument(
        "--broker",
        type=str,
        default=None,
        help="Specific broker to check (default: all brokers with live_equity rows).",
    )
    parser.add_argument(
        "--account",
        type=str,
        default=None,
        help="Optional account ID filter.",
    )
    parser.add_argument(
        "--include-testnet",
        action="store_true",
        help="Include testnet / sandbox equity rows (default: false).",
    )

    args = parser.parse_args()

    try:
        init_db()
    except Exception as e:
        print(f"Warning: DB init check failed ({e}), attempting to query directly...")

    with get_db_session() as session:
        if args.broker:
            metric = track_broker(
                session,
                broker=args.broker,
                account_id=args.account,
                include_testnet=args.include_testnet,
            )
            metrics = [metric] if metric is not None else []
        else:
            metrics = track_all_brokers(session, include_testnet=args.include_testnet)

    print()
    print("# Live vs Paper Execution Tracking")
    print()
    print(format_tracking_table(metrics))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
