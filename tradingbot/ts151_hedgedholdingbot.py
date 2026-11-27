"""TS151 Hedged Holding Bot (§10.1).

Source: Kakushadze & Serur (2018) §10.1 "Hedged holding".

Holds an underlying asset (e.g. XLK) hedged against market risk by shorting
a market ETF (e.g. SPY) with hedge ratio = cov(asset, hedge) / var(hedge).
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_commod import hedged_holding_weights

logger = logging.getLogger(__name__)


class TS151HedgedHoldingBot(TS151Bot):
    """Hedged Holding strategy (§10.1).

    Docstring:
    Kakushadze & Serur (2018) §10.1 "Hedged holding".
    Universe: asset ("XLK") and hedge ("SPY").
    Long `asset` (+1.0), short `hedge` (-beta) where
    beta = cov(asset, hedge) / var(hedge) over trailing `window` bars.
    Rebalance: Weekly ("W").
    """

    BOT_NAME: str = "ts151_HedgedHoldingBot"
    DEFAULT_UNIVERSE: tuple[str, ...] = ("XLK", "SPY")
    ALLOW_SHORT: ClassVar[bool] = True
    MAX_GROSS: ClassVar[float] = 2.5
    NEEDS_DB: bool = False
    NEEDS_NETWORK: bool = False

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "asset": "XLK",
        "hedge": "SPY",
        "window": 63,
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "asset": ["XLK"],
        "hedge": ["SPY"],
        "window": [42, 63, 126],
    }

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rebalance", "W")
        super().__init__(**kwargs)

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        """Calculate minimum-variance hedged weights between asset and hedge."""
        weights = hedged_holding_weights(
            ctx.closes,
            asset=self.asset,
            hedge=self.hedge,
            window=self.window,
        )
        if not weights:
            return None

        return weights
