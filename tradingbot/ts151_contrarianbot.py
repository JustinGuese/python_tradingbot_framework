"""TS151 Contrarian Bot (§10.3).

Source: Kakushadze & Serur (2018) §10.3 "Contrarian futures / trend ETFs".

Short-term mean-reversion across multi-asset trend ETFs.
Weights are proportional to -(R_i - mean(R)) over trailing `window` bars,
scaled to gross leverage `gross`.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_commod import contrarian_weights

logger = logging.getLogger(__name__)


class TS151ContrarianBot(TS151Bot):
    """Contrarian trend ETF strategy (§10.3).

    Docstring:
    Kakushadze & Serur (2018) §10.3 "Contrarian futures / trend ETFs".
    Universe: trend_etfs (SPY, QQQ, EFA, EEM, IEF, TLT, GLD, USO, DBA, UUP, FXE, FXY).
    Rebalance: Weekly ("W").
    Weights proportional to -(R_i - mean(R)) over window=5 bars, scaled to gross leverage 2.0.
    """

    BOT_NAME: str = "ts151_ContrarianBot"
    DEFAULT_UNIVERSE: str = "trend_etfs"
    ALLOW_SHORT: ClassVar[bool] = True
    MAX_GROSS: ClassVar[float] = 2.0
    NEEDS_DB: bool = False
    NEEDS_NETWORK: bool = False

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "window": 5,
        "gross": 2.0,
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "window": [3, 5, 10],
        "gross": [1.0, 2.0],
    }

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rebalance", "W")
        super().__init__(**kwargs)

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        """Compute dollar-neutral mean-reversion weights."""
        weights = contrarian_weights(
            ctx.closes,
            window=self.window,
            gross=self.gross,
        )
        if not weights:
            return None

        return weights
