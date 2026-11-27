"""TS151 Bullet Portfolio Bot (§5.2).

Source: Kakushadze & Serur (2018) §5.2 "Bullets".
Invests 100% in a single Treasury ETF whose maturity is nearest target_maturity.
Proxy: Treasury curve ETFs (SHY, IEI, IEF, TLH, TLT).
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_rates import bullet_weights

logger = logging.getLogger(__name__)


class TS151BulletsBot(TS151Bot):
    """Bullet Portfolio Strategy (§5.2).

    Kakushadze & Serur (2018) §5.2 "Bullets".
    Universe: treasury_curve (SHY, IEI, IEF, TLH, TLT).
    Rebalance: Monthly ("M").
    Allocates 100% into the ETF with maturity nearest target_maturity (default 8.5y -> IEF).
    """

    BOT_NAME: str = "ts151_BulletsBot"
    DEFAULT_UNIVERSE: str = "treasury_curve"
    ALLOW_SHORT: ClassVar[bool] = False
    LONG_ONLY: ClassVar[bool] = True
    MAX_GROSS: ClassVar[float] = 1.0
    LIVE_PERIOD: ClassVar[str] = "3y"
    BACKTEST_PERIOD: ClassVar[str | None] = "max"
    NEEDS_DB: bool = False
    NEEDS_NETWORK: bool = False

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "target_maturity": 8.5,
        "gross": 1.0,
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "target_maturity": [2.0, 5.0, 8.5, 15.0, 25.0],
        "gross": [1.0],
    }

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rebalance", "M")
        super().__init__(**kwargs)

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return bullet_weights(
            ctx.universe,
            target_maturity=float(self.target_maturity),
            gross=float(self.gross),
        )
