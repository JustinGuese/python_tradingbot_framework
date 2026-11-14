"""
BuybackBroadBot — long-only Form 8-K buyback authorization drift on broad universe.

Holds stocks across S&P 100, S&P 400, and S&P 600 for 10 sessions following
Form 8-K buyback authorizations (Item 8.01 / 7.01).

NO SHORTS: strictly long-only / flat. Weights are >= 0 at all times.
Idle cash is unallocated (parked in SHV / cash proxy by live copier).

Study & Evidence:
See docs/backtests/buyback-broad-2026-11.md. The broader universe (midsmall / broad)
exhibits beta >= 0.5 (0.56-0.62) and t < 2 (0.41-1.19), dragged down by negative
alpha in midcaps (-4.4%/yr in S&P 400). Per PLAN.md S3a bar (alpha > 0 in both halves
and beta < 0.5), this bot is NOT promoted to live/paper fleet; implementation is
maintained as unproven / research reference.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any, ClassVar

import pandas as pd
from sqlalchemy import func

from tradingbot.buybackdriftbot import read_buyback_events
from tradingbot.utils.botclass import Bot
from tradingbot.utils.buyback_rules import active_symbols, dedupe_recent
from tradingbot.utils.db import BuybackEvent, get_db_session
from tradingbot.utils.runner import run_bot

logger = logging.getLogger(__name__)

HOLD_SESSIONS = 10
DEFAULT_MAX_WEIGHT = 0.5
DEDUPE_DAYS = 30
PLACEHOLDER = "QQQ"


class BuybackBroadBot(Bot):
    """
    Broad-universe buyback drift bot (long-only, no shorts).

    Equal-weights active names capped at max_weight (default 0.5).
    Unallocated portfolio remains in cash (SHV proxy).
    """

    MIN_UNIVERSE_COVERAGE: ClassVar[float] = 0.5
    LIQUIDATE_UNTRACKED: ClassVar[bool] = True

    def __init__(
        self,
        events: list[Any] | None = None,
        tickers: list[str] | None = None,
        max_weight: float = DEFAULT_MAX_WEIGHT,
        hold_sessions: int = HOLD_SESSIONS,
        **kwargs,
    ):
        self.max_weight: float = max(0.0, float(max_weight))
        self.hold_sessions: int = int(hold_sessions)
        self._events: list[Any] | None = events

        if tickers is not None:
            active_tickers = list(tickers)
        elif events is not None:
            names = sorted({str(e["symbol"] if isinstance(e, dict) else e.symbol) for e in events})
            active_tickers = [*names, PLACEHOLDER] if PLACEHOLDER not in names else names
        else:
            # Fall back to placeholder if no tickers or events passed upfront;
            # dynamically populated on first bar if needed.
            active_tickers = [PLACEHOLDER]

        super().__init__(
            "BuybackBroadBot",
            tickers=active_tickers,
            interval="1d",
            period="1mo",
            **kwargs,
        )

    def _load_events(self) -> list[Any]:
        if self._events is not None:
            return self._events
        try:
            return read_buyback_events()
        except Exception as exc:
            logger.warning("%s: failed to load buyback events from DB: %s", self.bot_name, exc)
            return []

    @staticmethod
    def _bar_day(row: pd.Series) -> date:
        ts = row["timestamp"] if "timestamp" in row.index else row.name
        return pd.Timestamp(ts).date()

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """
        Compute long-only target weights.

        Rules:
        - Only active buyback names receive weight.
        - NO SHORTS: weights are strictly >= 0.
        - Each active name receives min(1.0 / n, max_weight).
        - Unallocated weight remains in cash (>= 0).
        """
        if not rows:
            return {}

        day = self._bar_day(next(iter(rows.values())))
        events = self._load_events()
        if not events:
            logger.info("%s: %s no events available -> 100%% cash", self.bot_name, day)
            return {}

        try:
            deduped = dedupe_recent(events, DEDUPE_DAYS)
        except (ValueError, TypeError, KeyError):
            deduped = events
        active = [
            s for s in active_symbols(deduped, day, hold_sessions=self.hold_sessions)
            if s != PLACEHOLDER
        ]

        if not active:
            logger.info("%s: %s no active buyback events -> 100%% cash", self.bot_name, day)
            return {}

        n = len(active)
        weight_per_symbol = min(1.0 / n, self.max_weight)
        # Ensure non-negative
        weight_per_symbol = max(0.0, weight_per_symbol)

        weights = {sym: weight_per_symbol for sym in active}

        # Strict invariant verification: NO SHORTS, caps respected
        for sym, w in weights.items():
            assert w >= 0.0, f"Negative weight detected for {sym}: {w}"
            assert w <= self.max_weight + 1e-6, f"Weight {w} exceeds cap {self.max_weight}"
        assert sum(weights.values()) <= 1.0 + 1e-6, f"Total weight exceeds 1.0: {sum(weights.values())}"

        logger.info(
            "%s: %s active=%s (n=%d, cap=%.2f) -> %s",
            self.bot_name,
            day,
            active,
            n,
            self.max_weight,
            weights,
        )
        return weights


if __name__ == "__main__":
    run_bot(BuybackBroadBot)
