"""TS151 Bond Carry and Roll-Down Bot (§5.11).

Source: Kakushadze & Serur (2018) §5.11 "Bond carry and roll-down".
Carry_i = (yield_i + roll-down_i) / duration_i.
Roll-down = (yield_i - yield_shorter) / (mat_i - mat_shorter) * horizon.
Long top k, short bottom k by carry.
Proxy: Treasury curve ETFs (SHY, IEI, IEF, TLH, TLT).
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_rates import bond_carry_weights, load_fred_treasury_yields

logger = logging.getLogger(__name__)


class TS151BondCarryBot(TS151Bot):
    """Bond Carry Strategy (§5.11).

    Kakushadze & Serur (2018) §5.11 "Bond carry and roll-down".
    Universe: treasury_curve (SHY, IEI, IEF, TLH, TLT).
    Rebalance: Monthly ("M").
    Ranks Treasury ETFs by (yield + roll-down) / duration.
    """

    BOT_NAME: str = "ts151_BondCarryBot"
    DEFAULT_UNIVERSE: str = "treasury_curve"
    ALLOW_SHORT: ClassVar[bool] = True
    LONG_ONLY: ClassVar[bool] = False
    MAX_GROSS: ClassVar[float] = 2.0
    LIVE_PERIOD: ClassVar[str] = "3y"
    BACKTEST_PERIOD: ClassVar[str | None] = "max"
    NEEDS_DB: bool = False
    NEEDS_NETWORK: bool = True

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "horizon": 1.0,
        "k": 2,
        "gross": 1.0,
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "horizon": [0.5, 1.0, 2.0],
        "k": [1, 2],
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
        return bond_carry_weights(
            last_yields,
            horizon=float(self.horizon),
            k=int(self.k),
            gross=float(self.gross),
        )
