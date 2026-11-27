"""TS151 Barbell Portfolio Bot (§5.3).

Source: Kakushadze & Serur (2018) §5.3 "Barbells".
Splits investment between short-term and long-term bonds so portfolio duration equals target_duration.
Proxy: SHY (short-term) and TLT (long-term).
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_rates import barbell_weights

logger = logging.getLogger(__name__)


class TS151BarbellsBot(TS151Bot):
    """Barbell Portfolio Strategy (§5.3).

    Kakushadze & Serur (2018) §5.3 "Barbells".
    Universe: SHY, TLT.
    Rebalance: Monthly ("M").
    Combines SHY and TLT such that target portfolio duration equals target_duration (default 7.5y).
    """

    BOT_NAME: str = "ts151_BarbellsBot"
    DEFAULT_UNIVERSE: tuple[str, ...] = ("SHY", "TLT")
    ALLOW_SHORT: ClassVar[bool] = False
    LONG_ONLY: ClassVar[bool] = True
    MAX_GROSS: ClassVar[float] = 1.0
    LIVE_PERIOD: ClassVar[str] = "3y"
    BACKTEST_PERIOD: ClassVar[str | None] = "max"
    NEEDS_DB: bool = False
    NEEDS_NETWORK: bool = False

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "target_duration": 7.5,
        "short_asset": "SHY",
        "long_asset": "TLT",
        "gross": 1.0,
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "target_duration": [5.0, 7.5, 10.0],
        "short_asset": ["SHY"],
        "long_asset": ["TLT"],
        "gross": [1.0],
    }

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rebalance", "M")
        super().__init__(**kwargs)

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return barbell_weights(
            target_duration=float(self.target_duration),
            short_asset=str(self.short_asset),
            long_asset=str(self.long_asset),
            gross=float(self.gross),
        )
