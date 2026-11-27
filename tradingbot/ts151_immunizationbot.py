"""TS151 Cash Flow Immunization Bot (§5.5).

Source: Kakushadze & Serur (2018) §5.5 "Immunization".
Matches remaining investment horizon duration using two bracketing Treasury ETFs.
The horizon declines from horizon_years by elapsed time since start.
When horizon <= shortest duration, allocates 100% to SHY.
Proxy: Treasury curve ETFs (SHY, IEI, IEF, TLH, TLT).
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_rates import immunization_weights

logger = logging.getLogger(__name__)


class TS151ImmunizationBot(TS151Bot):
    """Cash Flow Immunization Strategy (§5.5).

    Kakushadze & Serur (2018) §5.5 "Immunization".
    Universe: treasury_curve (SHY, IEI, IEF, TLH, TLT).
    Rebalance: Monthly ("M").
    Selects two ETFs bracketing the remaining horizon duration.
    """

    BOT_NAME: str = "ts151_ImmunizationBot"
    DEFAULT_UNIVERSE: str = "treasury_curve"
    ALLOW_SHORT: ClassVar[bool] = False
    LONG_ONLY: ClassVar[bool] = True
    MAX_GROSS: ClassVar[float] = 1.0
    LIVE_PERIOD: ClassVar[str] = "3y"
    BACKTEST_PERIOD: ClassVar[str | None] = "max"
    NEEDS_DB: bool = False
    NEEDS_NETWORK: bool = False

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "horizon_years": 7.0,
        "start": None,
        "gross": 1.0,
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "horizon_years": [3.0, 5.0, 7.0, 10.0],
        "start": [None],
        "gross": [1.0],
    }

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rebalance", "M")
        super().__init__(**kwargs)

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        start_date = self.start if self.start is not None else ctx.closes.index[0]
        return immunization_weights(
            as_of=ctx.as_of,
            start=start_date,
            horizon_years=float(self.horizon_years),
            universe=ctx.universe,
            gross=float(self.gross),
        )
