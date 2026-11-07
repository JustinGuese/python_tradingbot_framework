"""
BuybackDriftBot — holds S&P 100 stocks for 10 sessions following Form 8-K buyback authorizations.

Legs and evidence: docs/backtests/event-buyback-2026-11.md (10-session hold after an S&P 100
buyback authorization, +10%/yr alpha vs QQQ, t 2.56, placebo p 0.006-0.02; verdict: UNPROVEN).
Paper only: collects a genuine forward record, judged by weekly alphareport, not by return.

Deviation from study:
The backtest study held 100% of the book in a single active name when only one event was active.
Here, weights are equal-weighted among active symbols and capped at 0.5 (50%) each so a single
name is never the entire portfolio, keeping the remainder in cash when fewer than 2 names are active.

Schedule: 10 21 * * 1-5 (after the US close and the 21:00 buyback scan).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import logging
from typing import Any, ClassVar

import pandas as pd
from sqlalchemy import func

from tradingbot.utils.botclass import Bot
from tradingbot.utils.buyback_rules import active_symbols
from tradingbot.utils.db import BuybackEvent, get_db_session
from tradingbot.utils.runner import run_bot
from tradingbot.utils.universes import SP100

logger = logging.getLogger(__name__)


def read_buyback_events(
    since_days: int = 40,
    max_staleness_days: int = 10,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """
    Read events from buyback_events for the last `since_days` days.

    Raises RuntimeError if the buyback_events table is empty or the scanner has never run
    (MAX(created_at) older than max_staleness_days or no rows) so a dead scanner fails loudly.
    """
    ref_time = now or datetime.now(UTC)
    ref_naive = (
        ref_time.astimezone(UTC).replace(tzinfo=None)
        if ref_time.tzinfo is not None
        else ref_time
    )

    with get_db_session() as session:
        max_created = session.query(func.max(BuybackEvent.created_at)).scalar()
        if max_created is None:
            raise RuntimeError("buyback_events table is empty; scanner has never run")

        max_naive = (
            max_created.astimezone(UTC).replace(tzinfo=None)
            if max_created.tzinfo is not None
            else max_created
        )

        staleness_seconds = (ref_naive - max_naive).total_seconds()
        if staleness_seconds > max_staleness_days * 86400:
            raise RuntimeError(
                f"buyback scanner is stale: latest row created_at was {max_created} "
                f"({staleness_seconds / 86400:.1f} days ago > {max_staleness_days} days)"
            )

        cutoff = ref_naive - timedelta(days=since_days)
        rows = (
            session.query(BuybackEvent)
            .filter(BuybackEvent.filed_at >= cutoff)
            .order_by(BuybackEvent.filed_at.asc())
            .all()
        )
        return [
            {
                "id": r.id,
                "symbol": r.symbol,
                "accession": r.accession,
                "filed_at": r.filed_at,
                "source_url": r.source_url,
                "snippet": r.snippet,
                "created_at": r.created_at,
            }
            for r in rows
        ]


class BuybackDriftBot(Bot):
    """
    S&P 100 buyback drift bot: equal-weight active names capped at 0.5 each.
    """

    MIN_UNIVERSE_COVERAGE: ClassVar[float] = 0.95

    def __init__(self, events: list[Any] | None = None, **kwargs):
        super().__init__(
            "BuybackDriftBot",
            tickers=list(SP100),
            interval="1d",
            period="1mo",
            **kwargs,
        )
        self._events: list[Any] | None = events

    def _load_events(self) -> list[Any]:
        if self._events is not None:
            return self._events
        return read_buyback_events()

    @staticmethod
    def _bar_day(row: pd.Series) -> date:
        ts = row["timestamp"] if "timestamp" in row.index else row.name  # live vs backtest index differently
        return pd.Timestamp(ts).date()

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        if not rows:
            return {}
        day = self._bar_day(next(iter(rows.values())))
        events = self._load_events()
        active = active_symbols(events, day)
        if not active:
            logger.info("%s: %s no active buyback events -> cash", self.bot_name, day)
            return {}

        n = len(active)
        weight = min(1.0 / n, 0.5)
        weights = {sym: weight for sym in active}
        logger.info(
            "%s: %s active=%s (n=%d, cap=0.5) -> %s",
            self.bot_name,
            day,
            active,
            n,
            weights,
        )
        return weights


if __name__ == "__main__":
    run_bot(BuybackDriftBot)
