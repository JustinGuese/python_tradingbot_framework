"""BuybackSectorHedgedBot — S&P 100 buyback drift long book hedged by short sector ETFs.

Reuses BuybackDriftBot's 8-K buyback authorization signal on the long side (equal-weighted
active names capped at 0.5 each). Each active long is offset by a short position in its
corresponding sector ETF (from SECTORS9 via sector_map.sector_etf) of equal weight.

Idle cash capacity is parked in a cash proxy (SHV) via utils/cash_park.py, ensuring the
paper curve earns cash yield without leverage.

If no active buyback signals: returns {} (flat, no stranded short).

Legs and evidence: docs/backtests/buyback-sector-hedge-2026-11.md.
Paper only.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

import pandas as pd

from tradingbot.buybackdriftbot import BuybackDriftBot
from tradingbot.utils.botclass import Bot
from tradingbot.utils.cash_park import park_idle_cash
from tradingbot.utils.quantsmd_common import SECTORS9
from tradingbot.utils.runner import run_bot
from tradingbot.utils.sector_map import sector_etf
from tradingbot.utils.universes import SP100

logger = logging.getLogger(__name__)


class BuybackSectorHedgedBot(BuybackDriftBot):
    """S&P 100 buyback drift long book hedged by sector ETFs."""

    MIN_UNIVERSE_COVERAGE: ClassVar[float] = 0.95
    ALLOW_SHORT: ClassVar[bool] = True
    MAX_GROSS: ClassVar[float] = 2.0
    SHORTABLE: ClassVar[tuple[str, ...]] = tuple(SECTORS9)
    CASH_PROXY: ClassVar[str] = "SHV"

    def __init__(
        self,
        events: list[Any] | None = None,
        park_cash: bool = True,
        **kwargs: Any,
    ) -> None:
        tickers = list(dict.fromkeys((*SP100, *SECTORS9, self.CASH_PROXY)))
        Bot.__init__(
            self,
            "BuybackSectorHedgedBot",
            tickers=tickers,
            interval="1d",
            period="1mo",
            **kwargs,
        )
        self._events: list[Any] | None = events
        self._park_cash: bool = park_cash

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        long_weights = super().targetWeights(rows)
        if not long_weights:
            return {}

        weights: dict[str, float] = dict(long_weights)
        for sym, w in long_weights.items():
            sec = sector_etf(sym)
            weights[sec] = weights.get(sec, 0.0) - w

        if self._park_cash:
            weights = park_idle_cash(weights, proxy=self.CASH_PROXY, max_gross=self.MAX_GROSS)

        logger.info(
            "%s: hedged %d longs with sector ETFs -> %s",
            self.bot_name,
            len(long_weights),
            weights,
        )
        return weights


if __name__ == "__main__":
    run_bot(BuybackSectorHedgedBot)
