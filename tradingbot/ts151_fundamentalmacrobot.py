"""TS151 Fundamental Macro Bot (§19.2)."""

from __future__ import annotations

from typing import Any, ClassVar, Mapping, Sequence

from tradingbot.utils.ts151 import fred
from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_macro import (
    DEFAULT_MACRO_ALLOCATIONS,
    fundamental_macro_weights,
)


class TS151FundamentalMacroBot(TS151Bot):
    """Macroeconomic growth and inflation regime allocation (§19.2).

    Assigns assets into four regimes based on INDPRO and CPI YoY directions:
    - growth↑ infl↓: SPY / EFA
    - growth↑ infl↑: DBC / GLD / EEM
    - growth↓ infl↑: GLD / TIP / IEF
    - growth↓ infl↓: TLT / IEF
    """

    BOT_NAME = "ts151_fundamentalmacrobot"
    DEFAULT_UNIVERSE = "multi_asset"
    ALLOW_SHORT = False
    LONG_ONLY = True
    MAX_GROSS = 1.0
    NEEDS_NETWORK = True

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "window": 3,
        "allocations": DEFAULT_MACRO_ALLOCATIONS,
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "window": [1, 3, 6],
        "allocations": [DEFAULT_MACRO_ALLOCATIONS],
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
        self._indpro_series = None
        self._cpi_series = None

    def _get_macro(self):
        if self._indpro_series is None:
            try:
                self._indpro_series = fred.fred_series("INDPRO")
            except Exception:
                self._indpro_series = None
        if self._cpi_series is None:
            try:
                self._cpi_series = fred.fred_series("CPIAUCSL")
            except Exception:
                self._cpi_series = None
        return self._indpro_series, self._cpi_series

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        indpro = getattr(ctx, "indpro_series", None)
        cpi = getattr(ctx, "cpi_series", None)
        if indpro is None or cpi is None:
            net_indpro, net_cpi = self._get_macro()
            if indpro is None:
                indpro = net_indpro
            if cpi is None:
                cpi = net_cpi

        return fundamental_macro_weights(
            as_of=ctx.as_of,
            indpro_series=indpro,
            cpi_series=cpi,
            window=self.window,
            allocations=self.allocations,
        )
