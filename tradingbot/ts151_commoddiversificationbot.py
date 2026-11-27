"""TS151 Commodity Diversification Bot (§9.3).

Source: Kakushadze & Serur (2018) §9.3 "Commodity diversification".

Adding commodities to a stock/bond portfolio diversifies risk due to low correlation.
Allocates between SPY, IEF, GLD, and DBC using long-only minimum-variance
or equal-risk-contribution (ERC).
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_commod import commod_diversification_weights

logger = logging.getLogger(__name__)


class TS151CommodDiversificationBot(TS151Bot):
    """Commodity Diversification strategy (§9.3).

    Docstring:
    Kakushadze & Serur (2018) §9.3 "Commodity diversification".
    Universe: SPY, IEF, GLD, DBC.
    Long-only portfolio optimization: method="minvar" (minimum variance) or "erc" (equal risk contribution).
    Covariance window: 252 bars. Rebalance: Monthly ("M").
    """

    BOT_NAME: str = "ts151_CommodDiversificationBot"
    DEFAULT_UNIVERSE: tuple[str, ...] = ("SPY", "IEF", "GLD", "DBC")
    ALLOW_SHORT: ClassVar[bool] = False
    LONG_ONLY: ClassVar[bool] = True
    MAX_GROSS: ClassVar[float] = 1.0
    NEEDS_DB: bool = False
    NEEDS_NETWORK: bool = False

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "window": 252,
        "method": "minvar",
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "window": [63, 126, 252],
        "method": ["minvar", "erc"],
    }

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rebalance", "M")
        super().__init__(**kwargs)

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        """Compute optimal diversification weights across SPY, IEF, GLD, DBC."""
        w_series = commod_diversification_weights(
            ctx.closes,
            window=self.window,
            method=self.method,
        )
        if w_series.empty:
            return None

        return {str(k): float(v) for k, v in w_series.items()}
