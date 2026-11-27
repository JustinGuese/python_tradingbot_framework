"""Kakushadze & Serur (2018) §4.6: Multi-asset Trend strategy bot.

Selects assets across multi-asset universe with positive trailing return over
`window` bars (> 0). Allocates inverse-volatility weights over `vol_window` bars,
scaled to gross 1.0. Rebalanced monthly.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_etfs import multiasset_trend


class TS151MultiAssetTrendBot(TS151Bot):
    """Multi-asset Trend strategy bot (§4.6).

    Filters multi-asset universe to assets with positive trailing return over
    `window` bars, weighting them by inverse volatility over `vol_window`.
    """

    BOT_NAME = "ts151_MultiAssetTrendBot"
    DEFAULT_UNIVERSE = "multi_asset"
    BENCHMARKS = ()
    ALLOW_SHORT = False
    LONG_ONLY = True
    MAX_GROSS = 1.0
    BACKTEST_PERIOD = "max"
    LIVE_PERIOD = "3y"
    MIN_UNIVERSE_COVERAGE = 1.0

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "window": 252,
        "vol_window": 63,
        "gross": 1.0,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "window": [126, 252],
        "vol_window": [21, 63, 126],
    }

    def __init__(
        self,
        *,
        interval: str = "1d",
        period: str | None = None,
        rebalance: str = "M",
        universe: str | tuple[str, ...] | list[str] | None = None,
        long_only: bool | None = None,
        park_cash: bool | None = None,
        window: int = 252,
        vol_window: int = 63,
        gross: float = 1.0,
        **params: Any,
    ):
        super().__init__(
            interval=interval,
            period=period,
            rebalance=rebalance,
            universe=universe,
            long_only=long_only,
            park_cash=park_cash,
            window=window,
            vol_window=vol_window,
            gross=gross,
            **params,
        )

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return multiasset_trend(
            ctx,
            window=int(self.window),
            vol_window=int(self.vol_window),
            gross=float(self.gross),
        )
