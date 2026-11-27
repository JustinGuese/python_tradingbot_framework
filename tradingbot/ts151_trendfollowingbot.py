"""TS151 Trend Following Bot (§10.4).

Source: Kakushadze & Serur (2018) §10.4 "Trend following".

Multi-asset trend following with volatility targeting across trend ETFs.
Weight = sign(return over `window`) * (target_vol / N) / vol(`vol_window`),
capped at `max_gross` leverage.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_commod import trend_following_weights

logger = logging.getLogger(__name__)


class TS151TrendFollowingBot(TS151Bot):
    """Trend Following strategy (§10.4).

    Docstring:
    Kakushadze & Serur (2018) §10.4 "Trend following".
    Universe: trend_etfs (SPY, QQQ, EFA, EEM, IEF, TLT, GLD, USO, DBA, UUP, FXE, FXY).
    Rebalance: Monthly ("M").
    Weight per asset = sign(return over window=252) * (target_vol=0.4 / N) / vol(vol_window=63).
    Gross leverage capped at max_gross=2.0.
    """

    BOT_NAME: str = "ts151_TrendFollowingBot"
    DEFAULT_UNIVERSE: str = "trend_etfs"
    ALLOW_SHORT: ClassVar[bool] = True
    MAX_GROSS: ClassVar[float] = 2.0
    NEEDS_DB: bool = False
    NEEDS_NETWORK: bool = False

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "window": 252,
        "vol_window": 63,
        "target_vol": 0.4,
        "max_gross": 2.0,
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "window": [126, 252],
        "vol_window": [42, 63],
        "target_vol": [0.2, 0.4],
        "max_gross": [2.0],
    }

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rebalance", "M")
        super().__init__(**kwargs)

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        """Compute volatility-targeted trend-following weights."""
        weights = trend_following_weights(
            ctx.closes,
            window=self.window,
            vol_window=self.vol_window,
            target_vol=self.target_vol,
            max_gross=self.max_gross,
        )
        if not weights:
            return None

        return weights
