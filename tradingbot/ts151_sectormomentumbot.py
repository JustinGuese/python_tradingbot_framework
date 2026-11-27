"""Kakushadze & Serur (2018) §4.1: Sector Momentum strategy bot.

Ranks sector ETFs by trailing return over `window` bars ending `skip` bars ago.
Holds the top `k` names in equal weight with monthly rebalancing.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_etfs import sector_momentum


class TS151SectorMomentumBot(TS151Bot):
    """Sector Momentum strategy bot (§4.1).

    Ranks sector ETFs by return over `window` bars ending `skip` bars ago,
    holding top `k` names equally weighted.
    """

    BOT_NAME = "ts151_SectorMomentumBot"
    DEFAULT_UNIVERSE = "sectors"
    BENCHMARKS = ()
    ALLOW_SHORT = False
    LONG_ONLY = True
    MAX_GROSS = 1.0
    BACKTEST_PERIOD = "max"
    LIVE_PERIOD = "3y"
    MIN_UNIVERSE_COVERAGE = 1.0

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "window": 252,
        "skip": 21,
        "k": 3,
        "gross": 1.0,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "window": [63, 126, 252],
        "skip": [0, 5, 21],
        "k": [2, 3, 4],
    }

    def __init__(
        self,
        *,
        interval: str = "1d",
        period: str | None = None,
        rebalance: str = "M",
        universe: str | tuple[str, ...] | list[str] | None = None,
        long_only: bool | None = None,
        park_cash: bool | None = None,
        window: int = 252,
        skip: int = 21,
        k: int = 3,
        gross: float = 1.0,
        **params: Any,
    ):
        super().__init__(
            interval=interval,
            period=period,
            rebalance=rebalance,
            universe=universe,
            long_only=long_only,
            park_cash=park_cash,
            window=window,
            skip=skip,
            k=k,
            gross=gross,
            **params,
        )

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return sector_momentum(
            ctx,
            window=int(self.window),
            skip=int(self.skip),
            k=int(self.k),
            gross=float(self.gross),
        )
