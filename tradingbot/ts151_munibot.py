"""TS151 Municipal Bond Spread Bot (§13.1).

Source: Kakushadze & Serur (2018) §13.1 "Municipal bond arbitrage".
Long MUB, short IEF in duration-neutral amounts when MUB/IEF yield proxy = -log(MUB/IEF) z-score > entry=0.5.
Otherwise flat.
Proxy: MUB and IEF.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_rates import muni_signal

logger = logging.getLogger(__name__)


class TS151MuniBot(TS151Bot):
    """Municipal Bond Spread Strategy (§13.1).

    Kakushadze & Serur (2018) §13.1 "Municipal bond arbitrage".
    Universe: MUB, IEF.
    Rebalance: Daily ("D").
    Long MUB / short IEF duration-neutral when municipal yield spread is wide; else flat.
    """

    BOT_NAME: str = "ts151_MuniBot"
    DEFAULT_UNIVERSE: tuple[str, ...] = ("MUB", "IEF")
    ALLOW_SHORT: ClassVar[bool] = True
    LONG_ONLY: ClassVar[bool] = False
    MAX_GROSS: ClassVar[float] = 2.0
    LIVE_PERIOD: ClassVar[str] = "3y"
    BACKTEST_PERIOD: ClassVar[str | None] = "max"
    NEEDS_DB: bool = False
    NEEDS_NETWORK: bool = False

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "window": 252,
        "entry": 0.5,
        "duration_mub": 6.5,
        "duration_ief": 7.5,
        "gross": 1.0,
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "window": [126, 252],
        "entry": [0.25, 0.5, 1.0],
        "duration_mub": [6.5],
        "duration_ief": [7.5],
        "gross": [1.0],
    }

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rebalance", "D")
        super().__init__(**kwargs)

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return muni_signal(
            ctx.closes,
            window=int(self.window),
            entry=float(self.entry),
            duration_mub=float(self.duration_mub),
            duration_ief=float(self.duration_ief),
            gross=float(self.gross),
        )
