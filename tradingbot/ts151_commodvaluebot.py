"""TS151 Commodity Value Bot (§9.4).

Source: Kakushadze & Serur (2018) §9.4 "Commodity value".

Commodities mean-revert to the long-term marginal cost of production.
Value = log(mean price over [−1386, −1134]) - log(close), approximating
prices 4.5-5.5 years ago.
Long top k undervalued commodities, short bottom k overvalued commodities.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_commod import commod_value_scores, topk_bottomk

logger = logging.getLogger(__name__)


class TS151CommodValueBot(TS151Bot):
    """Commodity Value strategy (§9.4).

    Docstring:
    Kakushadze & Serur (2018) §9.4 "Commodity value".
    Universe: commodities ETF proxies (USO, UNG, GLD, SLV, CPER, DBA, CORN, WEAT, SOYB, CANE).
    Rebalance: Monthly ("M").
    Value = log(mean price over bars [-1386, -1134]) - log(current close).
    Requires LIVE_PERIOD="10y" to satisfy ~5.5-year history lookback.
    Long top k, short bottom k.
    """

    BOT_NAME: str = "ts151_CommodValueBot"
    DEFAULT_UNIVERSE: str = "commodities"
    ALLOW_SHORT: ClassVar[bool] = True
    MAX_GROSS: ClassVar[float] = 1.0
    LIVE_PERIOD: ClassVar[str] = "10y"
    BACKTEST_PERIOD: ClassVar[str | None] = "max"
    NEEDS_DB: bool = False
    NEEDS_NETWORK: bool = False

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "lag_start": 1386,
        "lag_end": 1134,
        "k": 3,
        "gross": 1.0,
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "lag_start": [1386],
        "lag_end": [1134],
        "k": [2, 3],
        "gross": [1.0],
    }

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rebalance", "M")
        super().__init__(**kwargs)

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        """Calculate long/short weights based on long-term commodity value."""
        scores = commod_value_scores(
            ctx.closes,
            lag_start=self.lag_start,
            lag_end=self.lag_end,
        )
        if len(scores) < 2 * self.k:
            return None

        return topk_bottomk(
            scores,
            k=self.k,
            long_only=self.long_only,
            gross=self.gross,
        )
