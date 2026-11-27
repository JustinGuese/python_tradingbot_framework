"""TS151 Global Fixed Income Bot (§19.4).

Source: Kakushadze & Serur (2018) §19.4 "Global fixed income".
Score = z(window=126 momentum) + z(-window=1260 reversal).
Long top k country bonds.
Proxy: country_bonds universe (IEF, BWX, IGOV, EMB).
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_rates import global_fixed_income_weights

logger = logging.getLogger(__name__)


class TS151GlobalFixedIncomeBot(TS151Bot):
    """Global Fixed Income Strategy (§19.4).

    Kakushadze & Serur (2018) §19.4 "Global fixed income".
    Universe: country_bonds (IEF, BWX, IGOV, EMB).
    Rebalance: Monthly ("M").
    Requires LIVE_PERIOD="10y" for the 5-year reversal lookback.
    Ranks sovereign bond markets by momentum plus multi-year reversal.
    """

    BOT_NAME: str = "ts151_GlobalFixedIncomeBot"
    DEFAULT_UNIVERSE: str = "country_bonds"
    ALLOW_SHORT: ClassVar[bool] = False
    LONG_ONLY: ClassVar[bool] = True
    MAX_GROSS: ClassVar[float] = 1.0
    LIVE_PERIOD: ClassVar[str] = "10y"
    BACKTEST_PERIOD: ClassVar[str | None] = "max"
    NEEDS_DB: bool = False
    NEEDS_NETWORK: bool = False

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "mom_window": 126,
        "rev_window": 1260,
        "k": 2,
        "gross": 1.0,
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "mom_window": [63, 126, 252],
        "rev_window": [756, 1260],
        "k": [1, 2],
        "gross": [1.0],
    }

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rebalance", "M")
        super().__init__(**kwargs)

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return global_fixed_income_weights(
            ctx.closes,
            mom_window=int(self.mom_window),
            rev_window=int(self.rev_window),
            k=int(self.k),
            gross=float(self.gross),
        )
