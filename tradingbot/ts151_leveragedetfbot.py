"""Kakushadze & Serur (2018) §4.5: Leveraged ETF Pairs strategy bot.

Shorts each bull/bear pair in equal dollars (total gross = 1.0).
Rebalances back to equal when legs drift > band (0.1), else rebalances monthly.

Paper trading only: these leveraged ETFs (TQQQ, SQQQ, SPXL, SPXS) are not
in TRADEABLE and carry financing/borrow constraints in live execution.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_etfs import leveraged_pair_rebalance


class TS151LeveragedEtfBot(TS151Bot):
    """Leveraged ETF Pairs short arbitrage bot (§4.5).

    Shorts pairs of bull/bear leveraged ETFs in equal notional amounts,
    harvesting leverage decay/volatility drag, rebalancing on monthly rolls or
    when legs drift beyond `band`.
    """

    BOT_NAME = "ts151_LeveragedEtfBot"
    DEFAULT_UNIVERSE = "leveraged_pairs"
    BENCHMARKS = ()
    ALLOW_SHORT = True
    LONG_ONLY = False
    MAX_GROSS = 1.0
    BACKTEST_PERIOD = "max"
    LIVE_PERIOD = "3y"
    MIN_UNIVERSE_COVERAGE = 1.0

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "band": 0.1,
        "gross": 1.0,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "band": [0.05, 0.1, 0.15],
        "gross": [0.5, 1.0],
    }

    def __init__(
        self,
        *,
        interval: str = "1d",
        period: str | None = None,
        rebalance: str = "D",
        universe: str | tuple[str, ...] | list[str] | None = None,
        long_only: bool | None = None,
        park_cash: bool | None = None,
        band: float = 0.1,
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
            band=band,
            gross=gross,
            **params,
        )

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return leveraged_pair_rebalance(
            ctx,
            band=float(self.band),
            gross=float(self.gross),
        )
