"""TS151 Commodity Skewness Bot (§9.5).

Source: Kakushadze & Serur (2018) §9.5 "Commodity skewness".

Assets with negative return skewness carry crash risk and command an expected return premium.
Assets with positive skewness exhibit lottery-like returns and command a price premium.
Long the most negative skewness (lowest skew), short the most positive skewness (highest skew).
Trades commodity ETF proxies.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_commod import skewness_scores, topk_bottomk

logger = logging.getLogger(__name__)


class TS151SkewnessBot(TS151Bot):
    """Commodity Skewness strategy (§9.5).

    Docstring:
    Kakushadze & Serur (2018) §9.5 "Commodity skewness".
    Universe: commodities ETF proxies (USO, UNG, GLD, SLV, CPER, DBA, CORN, WEAT, SOYB, CANE).
    Rebalance: Monthly ("M").
    Skewness of daily returns over window=252.
    Long top k most negative skew, short bottom k most positive skew.
    """

    BOT_NAME: str = "ts151_SkewnessBot"
    DEFAULT_UNIVERSE: str = "commodities"
    ALLOW_SHORT: ClassVar[bool] = True
    MAX_GROSS: ClassVar[float] = 1.0
    NEEDS_DB: bool = False
    NEEDS_NETWORK: bool = False

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "window": 252,
        "k": 3,
        "gross": 1.0,
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "window": [126, 252],
        "k": [2, 3],
        "gross": [1.0],
    }

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rebalance", "M")
        super().__init__(**kwargs)

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        """Calculate target weights from historical return skewness."""
        scores = skewness_scores(ctx.closes, window=self.window)
        if len(scores) < 2 * self.k:
            return None

        return topk_bottomk(
            scores,
            k=self.k,
            long_only=self.long_only,
            gross=self.gross,
        )
