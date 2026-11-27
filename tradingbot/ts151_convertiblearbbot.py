"""TS151 Convertible Arbitrage Bot (§12.1).

Source: Kakushadze & Serur (2018) §12.1 "Convertible arbitrage".
Long convertible bonds (CWB) and short underlying equity (SPY) scaled by rolling beta over window=126.
Proxy: CWB (convertible bond ETF) and SPY (equity index ETF).
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_rates import convertible_arb_weights

logger = logging.getLogger(__name__)


class TS151ConvertibleArbBot(TS151Bot):
    """Convertible Arbitrage Strategy (§12.1).

    Kakushadze & Serur (2018) §12.1 "Convertible arbitrage".
    Universe: CWB, SPY.
    Rebalance: Weekly ("W").
    Long CWB, short SPY scaled by trailing rolling regression beta.
    """

    BOT_NAME: str = "ts151_ConvertibleArbBot"
    DEFAULT_UNIVERSE: tuple[str, ...] = ("CWB", "SPY")
    ALLOW_SHORT: ClassVar[bool] = True
    LONG_ONLY: ClassVar[bool] = False
    MAX_GROSS: ClassVar[float] = 2.0
    LIVE_PERIOD: ClassVar[str] = "3y"
    BACKTEST_PERIOD: ClassVar[str | None] = "max"
    NEEDS_DB: bool = False
    NEEDS_NETWORK: bool = False

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "window": 126,
        "asset": "CWB",
        "hedge": "SPY",
        "gross": 1.0,
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "window": [63, 126, 252],
        "asset": ["CWB"],
        "hedge": ["SPY"],
        "gross": [1.0, 1.5],
    }

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rebalance", "W")
        super().__init__(**kwargs)

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return convertible_arb_weights(
            ctx.closes,
            window=int(self.window),
            asset=str(self.asset),
            hedge=str(self.hedge),
            gross=float(self.gross),
        )
