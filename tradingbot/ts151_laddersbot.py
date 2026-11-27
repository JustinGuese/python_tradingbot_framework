"""TS151 Ladder Portfolio Bot (§5.4).

Source: Kakushadze & Serur (2018) §5.4 "Ladders".
Equal weight across all Treasury curve ETFs.
Proxy: Treasury curve ETFs (SHY, IEI, IEF, TLH, TLT).
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_rates import ladder_weights

logger = logging.getLogger(__name__)


class TS151LaddersBot(TS151Bot):
    """Ladder Portfolio Strategy (§5.4).

    Kakushadze & Serur (2018) §5.4 "Ladders".
    Universe: treasury_curve (SHY, IEI, IEF, TLH, TLT).
    Rebalance: Monthly ("M").
    Invests equal weight across the maturity ladder.
    """

    BOT_NAME: str = "ts151_LaddersBot"
    DEFAULT_UNIVERSE: str = "treasury_curve"
    ALLOW_SHORT: ClassVar[bool] = False
    LONG_ONLY: ClassVar[bool] = True
    MAX_GROSS: ClassVar[float] = 1.0
    LIVE_PERIOD: ClassVar[str] = "3y"
    BACKTEST_PERIOD: ClassVar[str | None] = "max"
    NEEDS_DB: bool = False
    NEEDS_NETWORK: bool = False

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "gross": 1.0,
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "gross": [1.0],
    }

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rebalance", "M")
        super().__init__(**kwargs)

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return ladder_weights(
            ctx.universe,
            gross=float(self.gross),
        )
