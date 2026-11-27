"""Kakushadze & Serur (2018) §4.2: Alpha Rotation strategy bot.

Ranks sector ETFs by Jensen's alpha against SPY over `window` bars.
Holds top `k` names in equal weight with monthly rebalancing.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_etfs import alpha_rotation


class TS151AlphaRotationBot(TS151Bot):
    """Alpha Rotation strategy bot (§4.2).

    Computes Jensen's alpha of sector ETFs vs benchmark SPY over `window` bars,
    holding top `k` names equally weighted.
    """

    BOT_NAME = "ts151_AlphaRotationBot"
    DEFAULT_UNIVERSE = "sectors"
    BENCHMARKS = ("SPY",)
    ALLOW_SHORT = False
    LONG_ONLY = True
    MAX_GROSS = 1.0
    BACKTEST_PERIOD = "max"
    LIVE_PERIOD = "3y"
    MIN_UNIVERSE_COVERAGE = 1.0

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "window": 252,
        "k": 3,
        "benchmark": "SPY",
        "gross": 1.0,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "window": [63, 126, 252],
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
        k: int = 3,
        benchmark: str = "SPY",
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
            k=k,
            benchmark=benchmark,
            gross=gross,
            **params,
        )

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return alpha_rotation(
            ctx,
            window=int(self.window),
            k=int(self.k),
            benchmark=str(self.benchmark),
            gross=float(self.gross),
        )
