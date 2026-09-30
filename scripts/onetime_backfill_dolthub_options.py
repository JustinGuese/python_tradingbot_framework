"""
Backfill real historical AAPL/SPY option chains into `option_quotes` from the
free DoltHub `post-no-preference/options` mirror (utils/dolthub_options.py).

Every option-bot backtest to date (scripts/onetime_option_bots_backtest.py) has
priced options off a Black-Scholes/IV-proxy model, because yfinance has no
historical chains and our own capture (utils/option_capture.py) only started
on 2026-09-26. This is the fix: DoltHub's mirror has daily EOD chains (bid,
ask, IV, greeks) for AAPL and SPY back to 2019-02-09, live-checked through at
least 2026-09-25, for free and with no API key.

What lands in `option_quotes`: the same columns option_capture.py writes, so
every reader (utils/options.py, the backtests) works unmodified. There is no
open_interest, volume, last_price or spot price in the DoltHub source, so
those columns are NULL except underlying_price, which is filled from a
separate unadjusted daily-close fetch (DoltHub's strikes are NOT split
adjusted -- e.g. pre-Aug-2020 AAPL strikes run 115-420 against an unadjusted
~$321 close, so the moneyness match requires the unadjusted price series too).
snapshot_at is pinned to 21:00 UTC (after the close), which can never collide
with a same-day intraday live-capture row (19:45 UTC) -- both simply coexist
as separate snapshots of that day.

Requires POSTGRES_URI pointed at the real cluster Postgres (port-forward or
`kubectl exec`, see AGENTS.md) -- this writes real rows, there is no dry-run
mode. It IS safe to just re-run: every insert is ON CONFLICT DO NOTHING on
(contract_symbol, snapshot_at), and progress is checkpointed per symbol to
{RESEARCH_CACHE:-/tmp}/dolthub_backfill_progress.json so a killed run resumes
from its last completed batch instead of re-querying DoltHub from day one.

Usage:
    kubectl port-forward -n tradingbots-2025 svc/psql-service 5432:5432 &
    POSTGRES_URI=postgresql://postgres:<pw>@localhost:5432/postgres \\
        PYTHONPATH=. uv run python scripts/onetime_backfill_dolthub_options.py \\
        --symbols AAPL,SPY

Smoke test a small slice first with --limit-days (e.g. 10).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, timedelta

import yfinance as yf

from tradingbot.utils import dolthub_options as dh
from tradingbot.utils.config import setup_logging
from tradingbot.utils.db import init_db
from tradingbot.utils.option_quotes_repository import bulk_insert_quotes

logger = logging.getLogger(__name__)

CACHE = os.environ.get("RESEARCH_CACHE", "/tmp")
CHECKPOINT_PATH = os.path.join(CACHE, "dolthub_backfill_progress.json")


def _load_checkpoint() -> dict[str, str]:
    if os.path.exists(CHECKPOINT_PATH):
        with open(CHECKPOINT_PATH) as f:
            data: dict[str, str] = json.load(f)
            return data
    return {}


def _save_checkpoint(checkpoint: dict[str, str]) -> None:
    with open(CHECKPOINT_PATH, "w") as f:
        json.dump(checkpoint, f)


def _unadjusted_daily_closes(symbol: str, start: date, end: date) -> dict[date, float]:
    """Trading day -> unadjusted close. DoltHub strikes are pre-split, so the
    moneyness match needs the price as it traded that day.

    auto_adjust=False drops the dividend adjustment but NOT the split one:
    yfinance's Close is always split-adjusted. Until 2026-09-30 this stored
    pre-2020-08-31 AAPL spots at a quarter of the traded price beside
    unadjusted strikes (12,534 rows, corrected in place). Every split after the
    day is multiplied back in here."""
    raw = yf.download(
        symbol,
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        auto_adjust=False,
        progress=False,
    )
    if raw.empty:
        raise RuntimeError(f"No yfinance daily data for {symbol} {start}..{end}")
    if hasattr(raw.columns, "get_level_values"):
        raw.columns = raw.columns.get_level_values(0)
    splits = yf.Ticker(symbol).splits
    split_days = [(ts.date(), float(ratio)) for ts, ratio in splits.items() if ratio and ratio > 0]
    return {ts.date(): float(v) * split_factor(ts.date(), split_days) for ts, v in raw["Close"].items()}


def split_factor(day: date, splits: list[tuple[date, float]]) -> float:
    """Product of the split ratios that took effect after `day` (a 4:1 split is 4.0)."""
    factor = 1.0
    for when, ratio in splits:
        if when > day:
            factor *= ratio
    return factor


def backfill_symbol(
    symbol: str,
    start: date,
    end: date,
    batch_days: int,
    limit_days: int | None,
    checkpoint: dict[str, str],
    failed_batches: list[str],
) -> int:
    prices = _unadjusted_daily_closes(symbol, start, end)
    days = sorted(d for d in prices if start <= d <= end)

    resume_from = checkpoint.get(symbol)
    if resume_from:
        days = [d for d in days if d.isoformat() > resume_from]
    if limit_days is not None:
        days = days[:limit_days]
    if not days:
        logger.info("%s: nothing to do (checkpoint at %s)", symbol, resume_from or "start")
        return 0

    total_inserted = 0
    for i in range(0, len(days), batch_days):
        batch = days[i : i + batch_days]
        try:
            raw_rows = dh.fetch_chain_days(symbol, batch, batch_size=batch_days)
            quote_rows = dh.to_option_quote_rows(raw_rows, prices)
            inserted = bulk_insert_quotes(quote_rows)
        except Exception:
            # One batch's persistent failure (survived fetch_chain_days's own
            # RowLimit splitting) must not kill a multi-hour run over the rest
            # of the range -- log it, skip past it, and keep going, the same
            # way capture_universe lets one symbol fail without stopping the
            # others.
            logger.exception("%s: batch %s..%s failed, skipping", symbol, batch[0], batch[-1])
            failed_batches.append(f"{symbol} {batch[0]}..{batch[-1]}")
            checkpoint[symbol] = batch[-1].isoformat()
            _save_checkpoint(checkpoint)
            continue
        total_inserted += inserted
        checkpoint[symbol] = batch[-1].isoformat()
        _save_checkpoint(checkpoint)
        logger.info(
            "%s: %s..%s -> %d raw rows, %d inserted (symbol total %d)",
            symbol,
            batch[0],
            batch[-1],
            len(raw_rows),
            inserted,
            total_inserted,
        )
    return total_inserted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbols", default="AAPL,SPY", help="comma-separated underlyings")
    parser.add_argument("--start", default=dh.EARLIEST_DATE.isoformat())
    parser.add_argument("--end", default=(date.today() - timedelta(days=1)).isoformat())
    parser.add_argument("--batch-days", type=int, default=15, help="trading days per DoltHub query")
    parser.add_argument(
        "--limit-days",
        type=int,
        default=None,
        help="process only the first N trading days per symbol after the checkpoint (smoke test)",
    )
    args = parser.parse_args()

    setup_logging()
    init_db()

    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    checkpoint = _load_checkpoint()
    failed_batches: list[str] = []

    grand_total = 0
    for symbol in symbols:
        grand_total += backfill_symbol(symbol, start, end, args.batch_days, args.limit_days, checkpoint, failed_batches)
    logger.info(
        "Backfill done: %d rows inserted across %s, %d batches failed %s",
        grand_total,
        symbols,
        len(failed_batches),
        failed_batches,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
