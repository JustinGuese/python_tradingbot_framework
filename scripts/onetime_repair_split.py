"""
Repair a stock split that hit bot books before utils/splits.py existed: refund
the lost value in cash and restate portfolio_worth.

Written for VGT's 8:1 split on 2026-04-21. EarningsInsiderTiltBot and
RegimeAdaptiveBot held it; their share counts stayed put, so 7/8 of the position
vanished from their books that day. Both then rebalanced back to target, which
fixed the share count but not the lost value.

Per bot that held the symbol at the ex-date:
  * credit = qty_at_ex x (ratio - 1) x the ex-date close: exactly what the
    valuation dropped. It goes to cash rather than shares because the bots have
    long since re-bought their target weight; the next rebalance invests it.
  * portfolio_worth rows from the ex-date on get the same credit added to worth
    and holdings["USD"]. That removes the phantom one-day loss from the alpha
    series. It is a constant-cash approximation: the bot would have invested
    the money, not held it.
  * an applied_splits row (cash_credit set), so the split is never applied or
    refunded again. A second run is a no-op.

It also records the split in split_events and rescales the cached history.

qty_at_ex comes from two independent sources that must agree: the trade log
(the holding now minus trades since, as apply_splits computes it) and the last
portfolio_worth snapshot before the ex-date. A bot where they disagree is
skipped with an error.

The original portfolio_worth rows are written to --backup before any change.

Usage:
    kubectl port-forward -n tradingbots-2025 svc/psql-service 5432:5432 &
    PYTHONPATH=. uv run python scripts/onetime_repair_split.py VGT 2026-04-21 8 --backup vgt_backup.json
    (--dry-run prints the plan and writes nothing)
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, date, datetime, time, timedelta

import yfinance as yf

from tradingbot.utils import splits
from tradingbot.utils.config import setup_logging
from tradingbot.utils.db import AppliedSplit, Bot, HistoricData, PortfolioWorth, Trade, get_db_session, init_db

logger = logging.getLogger(__name__)

AGREE_TOL = 1e-6


def fetch_split(symbol: str, ex_date: date, ratio: float) -> splits.Split:
    """The split as yfinance reports it, which must match the ratio given."""
    df = yf.download(
        [symbol],
        start=str(ex_date - timedelta(days=10)),
        end=str(ex_date + timedelta(days=3)),
        actions=True,
        auto_adjust=True,
        group_by="ticker",
        progress=False,
    )
    found = [s for s in splits.parse_split_frame(df) if s.ex_date == ex_date]
    if not found or abs(found[0].ratio - ratio) > 1e-9:
        raise SystemExit(f"yfinance does not report a {ratio:g}:1 split of {symbol} on {ex_date}: {found}")
    return found[0]


def ex_date_close(session, symbol: str, ex_date: date) -> float:
    row = (
        session.query(HistoricData.close)
        .filter(
            HistoricData.symbol == symbol,
            HistoricData.interval == "1d",
            HistoricData.timestamp >= datetime.combine(ex_date, time()),
            HistoricData.timestamp < datetime.combine(ex_date + timedelta(days=1), time()),
        )
        .first()
    )
    if row is None or not row[0]:
        raise SystemExit(f"No stored 1d close for {symbol} on {ex_date}")
    return float(row[0])


def snapshot_qty(session, bot_name: str, split: splits.Split) -> float:
    """Holding in the last portfolio_worth row before the ex-date, plus pre-split trades after it."""
    row = (
        session.query(PortfolioWorth)
        .filter(PortfolioWorth.bot_name == bot_name, PortfolioWorth.date < datetime.combine(split.ex_date, time()))
        .order_by(PortfolioWorth.date.desc())
        .first()
    )
    if row is None:
        return 0.0
    qty = float((row.holdings or {}).get(split.symbol, 0.0))
    taken = row.created_at or row.date + timedelta(hours=22)  # the job runs at 22:00 UTC
    trades = (
        session.query(Trade.timestamp, Trade.quantity, Trade.price, Trade.isBuy)
        .filter(Trade.bot_name == bot_name, Trade.symbol == split.symbol, Trade.timestamp > taken)
        .all()
    )
    qty += sum(
        (q or 0.0) * (1 if buy else -1) for ts, q, p, buy in trades if not splits.traded_after_split(ts, p, split)
    )
    return qty


def repair_bot(session, bot_name: str, split: splits.Split, close: float, dry_run: bool, backup: list) -> float | None:
    """Refund one bot. Returns the credit, 0.0 if it did not hold the symbol, None if skipped."""
    if session.get(AppliedSplit, (bot_name, split.symbol, split.ex_date)) is not None:
        logger.info("%s: already handled", bot_name)
        return None
    row = session.query(Bot).filter_by(name=bot_name).with_for_update().one()
    portfolio = dict(row.portfolio or {})
    from_log = splits.shares_at_ex(session, bot_name, split, float(portfolio.get(split.symbol, 0.0)))
    from_snapshot = snapshot_qty(session, bot_name, split)
    if abs(from_log - from_snapshot) > AGREE_TOL:
        logger.error(
            "%s: trade log says %.8f held at the ex-date, snapshot %.8f — skipped", bot_name, from_log, from_snapshot
        )
        return None
    qty = from_log
    credit = qty * (split.ratio - 1.0) * close if abs(qty) > AGREE_TOL else 0.0
    rows = []
    if credit:
        rows = (
            session.query(PortfolioWorth)
            .filter(PortfolioWorth.bot_name == bot_name, PortfolioWorth.date >= datetime.combine(split.ex_date, time()))
            .order_by(PortfolioWorth.date)
            .all()
        )
        logger.info(
            "%s: held %.8f %s at the ex-date -> refund $%.2f (%.8f x %g x %.2f), restating %d portfolio_worth rows",
            bot_name,
            qty,
            split.symbol,
            credit,
            qty,
            split.ratio - 1,
            close,
            len(rows),
        )
    if dry_run:
        return credit
    for pw in rows:
        backup.append(
            {
                "bot_name": bot_name,
                "date": pw.date.isoformat(),
                "portfolio_worth": pw.portfolio_worth,
                "holdings": dict(pw.holdings),
            }
        )
        pw.portfolio_worth = pw.portfolio_worth + credit
        holdings = dict(pw.holdings)
        holdings["USD"] = holdings.get("USD", 0.0) + credit
        pw.holdings = holdings
    if credit:
        portfolio["USD"] = portfolio.get("USD", 0.0) + credit
        row.portfolio = portfolio
    today = datetime.now(UTC).date()
    session.add(
        AppliedSplit(
            bot_name=bot_name,
            symbol=split.symbol,
            ex_date=split.ex_date,
            ratio=split.ratio,
            qty_at_ex=qty,
            qty_added=0.0,
            cash_credit=credit or None,
            note=f"refunded in cash on {today} (scripts/onetime_repair_split.py); "
            f"portfolio_worth from {split.ex_date} restated +{credit:.2f}"
            if credit
            else None,
        )
    )
    return credit


def main() -> None:
    parser = argparse.ArgumentParser(description="Refund a missed stock split (see module docstring).")
    parser.add_argument("symbol")
    parser.add_argument("ex_date", type=date.fromisoformat)
    parser.add_argument("ratio", type=float)
    parser.add_argument("--backup", default="split_repair_backup.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    setup_logging()
    init_db()
    split = fetch_split(args.symbol, args.ex_date, args.ratio)
    logger.info("Split: %s", split)

    if not args.dry_run:
        with get_db_session() as session:
            splits.record_splits(session, [split])
        logger.info("History rescaled: %s", splits.adjust_pending_history())

    with get_db_session() as session:
        names = [n for (n,) in session.query(Bot.name).order_by(Bot.name)]
        close = ex_date_close(session, split.symbol, split.ex_date)
    logger.info("%s close on %s: %.4f", split.symbol, split.ex_date, close)

    backup: list = []
    total = 0.0
    for name in names:
        with get_db_session() as session:
            credit = repair_bot(session, name, split, close, args.dry_run, backup)
            if credit:
                total += credit
            if backup and not args.dry_run:
                # Written inside the transaction, before the commit.
                with open(args.backup, "a") as f:
                    for b in backup:
                        f.write(json.dumps(b) + "\n")
                backup.clear()
    logger.info("%s: total refunded $%.2f", "DRY RUN" if args.dry_run else "DONE", total)


if __name__ == "__main__":
    main()
