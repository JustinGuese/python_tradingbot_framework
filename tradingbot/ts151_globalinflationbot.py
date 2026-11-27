"""TS151 Global Inflation Bot (§19.3)."""

from __future__ import annotations

from typing import Any, ClassVar, Mapping

from tradingbot.utils.ts151 import fred
from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_macro import global_inflation_weights


class TS151GlobalInflationBot(TS151Bot):
    """Dynamic inflation hedge allocation using rolling inflation beta (§19.3).

    When CPI YoY exceeds threshold or is rising, weights are allocated proportional
    to rolling regression beta of monthly asset returns on CPI change across
    inflation hedge assets (TIP, GLD, DBC, VNQ, XLE).
    Otherwise, defaults to a 60/40 benchmark (SPY/IEF).
    """

    BOT_NAME = "ts151_globalinflationbot"
    DEFAULT_UNIVERSE = ("TIP", "GLD", "DBC", "VNQ", "XLE", "SPY", "IEF")
    ALLOW_SHORT = False
    LONG_ONLY = True
    MAX_GROSS = 1.0
    LIVE_PERIOD = "5y"
    NEEDS_NETWORK = True

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "threshold": 3.0,
        "window": 60,
        "trend_window": 3,
        "hedge_universe": ("TIP", "GLD", "DBC", "VNQ", "XLE"),
        "benchmark_weights": {"SPY": 0.60, "IEF": 0.40},
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "threshold": [2.5, 3.0, 3.5],
        "window": [36, 60],
        "trend_window": [1, 3, 6],
        "hedge_universe": [("TIP", "GLD", "DBC", "VNQ", "XLE")],
        "benchmark_weights": [{"SPY": 0.60, "IEF": 0.40}],
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

        return global_inflation_weights(
            closes=ctx.closes,
            as_of=ctx.as_of,
            cpi_series=cpi,
            threshold=self.threshold,
            window=self.window,
            trend_window=self.trend_window,
            hedge_universe=self.hedge_universe,
            benchmark_weights=self.benchmark_weights,
        )
