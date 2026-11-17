"""
BuybackHedgedBot — S&P 100 buyback drift long book hedged by a short QQQ position.

Reuses BuybackDriftBot's 8-K buyback authorization signal on the long side (equal-weighted
active names capped at 0.5 each). Plus a short QQQ position sized to beta ~0 (sum of long
weights hedged by -sum(long_weights) on QQQ, so signed sum of weights + cash = 1.0,
gross <= 2.0).

If no active buyback signals: returns {} (flat, no stranded short).

Legs and evidence: docs/backtests/event-buyback-2026-11.md.
Paper only.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

import pandas as pd

from tradingbot.buybackdriftbot import BuybackDriftBot, read_buyback_events
from tradingbot.utils.botclass import Bot
from tradingbot.utils.runner import run_bot
from tradingbot.utils.universes import SP100

logger = logging.getLogger(__name__)


class BuybackHedgedBot(BuybackDriftBot):
    """
    S&P 100 buyback drift long book hedged by a short QQQ position sized to beta ~0.
    """

    MIN_UNIVERSE_COVERAGE: ClassVar[float] = 0.95
    ALLOW_SHORT: ClassVar[bool] = True
    MAX_GROSS: ClassVar[float] = 2.0
    SHORTABLE: ClassVar[tuple[str, ...]] = ("QQQ",)

    def __init__(self, events: list[Any] | None = None, **kwargs: Any) -> None:
        tickers = list(dict.fromkeys((*SP100, "QQQ")))
        Bot.__init__(
            self,
            "BuybackHedgedBot",
            tickers=tickers,
            interval="1d",
            period="1mo",
            **kwargs,
        )
        self._events: list[Any] | None = events

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        long_weights = super().targetWeights(rows)
        if not long_weights:
            return {}

        total_long = sum(long_weights.values())
        weights = dict(long_weights)
        weights["QQQ"] = -total_long
        logger.info(
            "%s: hedged long=%.4f with QQQ=%.4f -> %s",
            self.bot_name,
            total_long,
            -total_long,
            weights,
        )
        return weights


if __name__ == "__main__":
    run_bot(BuybackHedgedBot)
