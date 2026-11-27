"""TS151 Riding the Yield Curve (Rolling Down) Bot (§5.12).

Source: Kakushadze & Serur (2018) §5.12 "Riding the yield curve".
Holds the single Treasury ETF with the highest roll-down per unit duration.
Proxy: Treasury curve ETFs (SHY, IEI, IEF, TLH, TLT).
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_rates import load_fred_treasury_yields, rolling_down_weights

logger = logging.getLogger(__name__)


class TS151RollingDownBot(TS151Bot):
    """Riding the Yield Curve Strategy (§5.12).

    Kakushadze & Serur (2018) §5.12 "Riding the yield curve".
    Universe: treasury_curve (SHY, IEI, IEF, TLH, TLT).
    Rebalance: Monthly ("M").
    Long-only single ETF with highest roll-down per unit duration.
    """

    BOT_NAME: str = "ts151_RollingDownBot"
    DEFAULT_UNIVERSE: str = "treasury_curve"
    ALLOW_SHORT: ClassVar[bool] = False
    LONG_ONLY: ClassVar[bool] = True
    MAX_GROSS: ClassVar[float] = 1.0
    LIVE_PERIOD: ClassVar[str] = "3y"
    BACKTEST_PERIOD: ClassVar[str | None] = "max"
    NEEDS_DB: bool = False
    NEEDS_NETWORK: bool = True

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "horizon": 1.0,
        "gross": 1.0,
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "horizon": [0.5, 1.0, 2.0],
        "gross": [1.0],
    }

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rebalance", "M")
        super().__init__(**kwargs)

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        try:
            yields_df = load_fred_treasury_yields(ctx.closes.index)
        except Exception as exc:
            logger.warning("Could not load FRED yields: %s", exc)
            return None

        if yields_df.empty:
            return None

        last_yields = yields_df.iloc[-1].to_dict()
        return rolling_down_weights(
            last_yields,
            horizon=float(self.horizon),
            gross=float(self.gross),
        )
