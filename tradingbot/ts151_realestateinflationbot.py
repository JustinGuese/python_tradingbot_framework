"""TS151 Real Estate Inflation Bot (§16.5)."""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.ts151 import fred
from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_macro import real_estate_inflation_weights


class TS151RealEstateInflationBot(TS151Bot):
    """Tactical regime allocation between REITs and Treasuries (§16.5).

    Holds VNQ when CPI YoY (from FRED CPIAUCSL) is rising over `window` months,
    otherwise shifts to Treasuries (IEF).
    """

    BOT_NAME = "ts151_realestateinflationbot"
    DEFAULT_UNIVERSE = ("VNQ", "IEF")
    ALLOW_SHORT = False
    LONG_ONLY = True
    MAX_GROSS = 1.0
    NEEDS_NETWORK = True

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "window": 3,
        "asset_rising": "VNQ",
        "asset_falling": "IEF",
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "window": [1, 3, 6, 12],
        "asset_rising": ["VNQ"],
        "asset_falling": ["IEF"],
    }

    def __init__(
        self,
        *,
        interval: str = "1d",
        period: str | None = None,
        rebalance: str = "M",
        universe: str | tuple[str, ...] | list[str] | None = None,
        long_only: bool = True,
        park_cash: bool = False,
        **params: Any,
    ):
        super().__init__(
            interval=interval,
            period=period,
            rebalance=rebalance,
            universe=universe,
            long_only=long_only,
            park_cash=park_cash,
            **params,
        )
        self._cpi_series = None

    def _get_cpi(self):
        if self._cpi_series is None:
            try:
                self._cpi_series = fred.fred_series("CPIAUCSL")
            except Exception:
                self._cpi_series = None
        return self._cpi_series

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        cpi = getattr(ctx, "cpi_series", None)
        if cpi is None:
            cpi = self._get_cpi()

        return real_estate_inflation_weights(
            as_of=ctx.as_of,
            cpi_series=cpi,
            window=self.window,
            asset_rising=self.asset_rising,
            asset_falling=self.asset_falling,
        )
