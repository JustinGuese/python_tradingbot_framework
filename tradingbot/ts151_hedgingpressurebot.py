"""TS151 Hedging Pressure Bot (§9.2).

Source: Kakushadze & Serur (2018) §9.2 "Hedging pressure".

Hedging pressure (HP) = comm_long / (comm_long + comm_short),
averaged over the last `weeks` CFTC reports.
Per §9.2: Commercial hedgers shorting (producers) pay a risk premium to longs.
Long the lowest HP (producers hedging net short), short the highest HP (consumers hedging).
Trades commodity ETF proxies.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

import pandas as pd

from tradingbot.utils.ts151 import data_api, universes
from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_commod import hedging_pressure_signal

logger = logging.getLogger(__name__)


class TS151HedgingPressureBot(TS151Bot):
    """Hedging Pressure strategy (§9.2).

    Docstring:
    Kakushadze & Serur (2018) §9.2 "Hedging pressure".
    Universe: commodities ETF proxies (USO, UNG, GLD, SLV, CPER, DBA, CORN, WEAT, SOYB, CANE).
    Rebalance: Weekly ("W").
    Hedging pressure = comm_long / (comm_long + comm_short) from CFTC COT reports.
    Long k lowest HP (hedgers net short -> positive risk premium), short k highest HP.
    """

    BOT_NAME: str = "ts151_HedgingPressureBot"
    DEFAULT_UNIVERSE: str = "commodities"
    ALLOW_SHORT: ClassVar[bool] = True
    MAX_GROSS: ClassVar[float] = 1.0
    NEEDS_DB: bool = True
    NEEDS_NETWORK: bool = False

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "weeks": 4,
        "k": 3,
        "gross": 1.0,
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "weeks": [2, 4, 8],
        "k": [2, 3],
        "gross": [1.0],
    }

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rebalance", "W")
        super().__init__(**kwargs)
        self._cot_cache: pd.DataFrame | None = None

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        """Calculate hedging pressure weights from point-in-time COT data."""
        if self._cot_cache is None:
            codes = list(universes.COT_CODES.values())
            self._cot_cache = data_api.load_cot(codes)

        if self._cot_cache is None or self._cot_cache.empty:
            return None

        # Point-in-time filter (release lag 3 days: Tuesday report released Friday)
        cot_pit = data_api.cot_asof(self._cot_cache, ctx.as_of, release_lag_days=3)
        if cot_pit.empty:
            return None

        weights = hedging_pressure_signal(
            cot_pit,
            universes.COT_CODES,
            weeks=self.weeks,
            k=self.k,
            gross=self.gross,
            long_only=self.long_only,
        )
        if not weights:
            return None

        return weights
