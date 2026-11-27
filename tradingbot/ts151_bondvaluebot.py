"""TS151 Bond Value Bot (§5.10).

Source: Kakushadze & Serur (2018) §5.10 "Bond value".
Value = current FRED yield − 5-year (window=1260) historical average yield.
Long top k undervalued (high yield excess), short bottom k overvalued (low yield excess).
Proxy: Treasury curve ETFs (SHY, IEI, IEF, TLH, TLT).
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_rates import bond_value_weights, load_fred_treasury_yields

logger = logging.getLogger(__name__)


class TS151BondValueBot(TS151Bot):
    """Bond Value Strategy (§5.10).

    Kakushadze & Serur (2018) §5.10 "Bond value".
    Universe: treasury_curve (SHY, IEI, IEF, TLH, TLT).
    Rebalance: Monthly ("M").
    Requires LIVE_PERIOD="10y" for the 5-year lookback.
    Long top k, short bottom k.
    """

    BOT_NAME: str = "ts151_BondValueBot"
    DEFAULT_UNIVERSE: str = "treasury_curve"
    ALLOW_SHORT: ClassVar[bool] = True
    LONG_ONLY: ClassVar[bool] = False
    MAX_GROSS: ClassVar[float] = 2.0
    LIVE_PERIOD: ClassVar[str] = "10y"
    BACKTEST_PERIOD: ClassVar[str | None] = "max"
    NEEDS_DB: bool = False
    NEEDS_NETWORK: bool = True

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "window": 1260,
        "k": 2,
        "gross": 1.0,
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "window": [756, 1260],
        "k": [1, 2],
        "gross": [1.0, 1.5],
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

        return bond_value_weights(
            yields_df,
            window=int(self.window),
            k=int(self.k),
            gross=float(self.gross),
        )
