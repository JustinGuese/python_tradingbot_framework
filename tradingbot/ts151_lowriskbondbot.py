"""TS151 Low-Risk Bond Bot (§5.9).

Source: Kakushadze & Serur (2018) §5.9 "Low-risk bond strategy".
Long the lowest-volatility bonds and short the highest-volatility bonds,
each side weighted by inverse volatility so each asset contributes equally to risk.
Proxy: bond_etfs universe.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_rates import low_risk_bond_weights

logger = logging.getLogger(__name__)


class TS151LowRiskBondBot(TS151Bot):
    """Low-Risk Bond Strategy (§5.9).

    Kakushadze & Serur (2018) §5.9 "Low-risk bond strategy".
    Universe: bond_etfs.
    Rebalance: Monthly ("M").
    Long lowest-vol frac=0.33 and short highest-vol frac=0.33, equal-vol weighted.
    """

    BOT_NAME: str = "ts151_LowRiskBondBot"
    DEFAULT_UNIVERSE: str = "bond_etfs"
    ALLOW_SHORT: ClassVar[bool] = True
    LONG_ONLY: ClassVar[bool] = False
    MAX_GROSS: ClassVar[float] = 2.0
    LIVE_PERIOD: ClassVar[str] = "3y"
    BACKTEST_PERIOD: ClassVar[str | None] = "max"
    NEEDS_DB: bool = False
    NEEDS_NETWORK: bool = False

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "frac": 0.33,
        "vol_window": 63,
        "gross": 2.0,
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "frac": [0.25, 0.33, 0.5],
        "vol_window": [42, 63, 126],
        "gross": [1.0, 2.0],
    }

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rebalance", "M")
        super().__init__(**kwargs)

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return low_risk_bond_weights(
            ctx.closes,
            frac=float(self.frac),
            vol_window=int(self.vol_window),
            gross=float(self.gross),
        )
