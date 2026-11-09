"""
Activist13DBot: holds S&P 100/400/600 names for 20 sessions after a fund-like initial Schedule 13D filing (paper).

Evidence: docs/backtests/event-13d-2026-11.md. The pre-registered primary test (fund-like filer, 20 sessions, 10 bp per
side, 2010-2026, 410 events) did NOT pass: alpha vs QQQ +18.6%/yr but t 1.60, beta 0.68, same-name placebo p 0.0475. The
broader "all filers" books look better at book level but are carried by a few outliers (event-level median ~0). Verdict:
UNPROVEN, built on request as a forward test. Judge it by the weekly alphareport, not by return; expect `too short`.

Deviation from the study: weights are equal among the active names but capped at 0.5 each, so one filing is never the
whole book (the study held 100% of a lone active name). With ~1.7 names active on average the book is often part cash.

How the universe works: the watch list is ~1,100 names but only a handful are ever held, so each run's ticker list is
the fund-like 13D targets filed in the last 45 days (a superset of the 20-session window) plus one placeholder ticker
(Bot refuses a ticker list with nothing tradeable; the placeholder is never given a weight). With
LIQUIDATE_UNTRACKED set, a holding that has aged out of the list is sold.

Schedule: after the 20:45 UTC sc13dscan CronJob and the US close.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any, ClassVar

import pandas as pd
from sqlalchemy import func

from tradingbot.utils.botclass import Bot
from tradingbot.utils.buyback_rules import active_symbols, dedupe_recent
from tradingbot.utils.db import Sc13dEvent, get_db_session
from tradingbot.utils.runner import run_bot

logger = logging.getLogger(__name__)

HOLD_SESSIONS = 20
MAX_WEIGHT = 0.5
WINDOW_DAYS = 45
DEDUPE_DAYS = 30
PLACEHOLDER = "QQQ"


def read_sc13d_events(
    since_days: int = WINDOW_DAYS,
    max_staleness_days: int = 10,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """
    Fund-like initial 13D events filed in the last ``since_days`` days.

    Raises RuntimeError when the table is empty or the newest row (the scanner's heartbeat included) is older than
    ``max_staleness_days``, so a dead scanner fails loudly instead of reading as a quiet market.
    """
    ref = now or datetime.now(UTC)
    ref_naive = ref.astimezone(UTC).replace(tzinfo=None) if ref.tzinfo is not None else ref

    with get_db_session() as session:
        newest = session.query(func.max(Sc13dEvent.created_at)).scalar()
        if newest is None:
            raise RuntimeError("sc13d_events table is empty; scanner has never run")
        newest_naive = newest.astimezone(UTC).replace(tzinfo=None) if newest.tzinfo is not None else newest
        age_days = (ref_naive - newest_naive).total_seconds() / 86400
        if age_days > max_staleness_days:
            raise RuntimeError(
                f"13D scanner is stale: latest row created_at was {newest} ({age_days:.1f} days ago "
                f"> {max_staleness_days} days)"
            )
        rows = (
            session.query(Sc13dEvent)
            .filter(Sc13dEvent.filed_at >= ref_naive - timedelta(days=since_days), Sc13dEvent.fund_like.is_(True))
            .order_by(Sc13dEvent.filed_at.asc())
            .all()
        )
        return [
            {"id": r.id, "symbol": r.symbol, "accession": r.accession, "filed_at": r.filed_at, "filers": r.filers}
            for r in rows
        ]


class Activist13DBot(Bot):
    """Equal-weight (cap 0.5) book of names within 20 sessions of a fund-like initial 13D filing."""

    # Missing data for a name (a target acquired after the filing, say) must not skip the whole run.
    MIN_UNIVERSE_COVERAGE: ClassVar[float] = 0.5
    LIQUIDATE_UNTRACKED: ClassVar[bool] = True

    def __init__(self, events: list[Any] | None = None, **kwargs):
        self._events: list[Any] = read_sc13d_events() if events is None else list(events)
        names = sorted({str(e["symbol"] if isinstance(e, dict) else e.symbol) for e in self._events})
        super().__init__(
            "Activist13DBot",
            tickers=[*names, PLACEHOLDER] if PLACEHOLDER not in names else names,
            interval="1d",
            period="1mo",
            **kwargs,
        )

    @staticmethod
    def _bar_day(row: pd.Series) -> date:
        ts = row["timestamp"] if "timestamp" in row.index else row.name  # live vs backtest index differently
        return pd.Timestamp(ts).date()

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        if not rows:
            return {}
        day = self._bar_day(next(iter(rows.values())))
        events = dedupe_recent(self._events, DEDUPE_DAYS)
        active = [s for s in active_symbols(events, day, HOLD_SESSIONS) if s != PLACEHOLDER]
        if not active:
            logger.info("%s: %s no active fund-like 13D events -> cash", self.bot_name, day)
            return {}
        weights = dict.fromkeys(active, min(1.0 / len(active), MAX_WEIGHT))
        logger.info("%s: %s active=%s -> %s", self.bot_name, day, active, weights)
        return weights


if __name__ == "__main__":
    run_bot(Activist13DBot)
