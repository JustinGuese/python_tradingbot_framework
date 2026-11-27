"""Kakushadze & Serur (2018) §3.9: Mean Reversion with Industry Clusters.

Returns over `window=5`, demeaned within sector.
Weights ∝ -demeaned, dollar-neutral per sector, gross 2.0.
Defaults: window=5, gross=2.0.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.runner import run_bot
from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_stocks import compute_mean_reversion_cluster_weights


class TS151MeanReversionClusterBot(TS151Bot):
    """Mean Reversion with Industry Clusters (§3.9).

    Exploits short-term within-industry return reversals. Demeans trailing window returns
    by sector average and takes contrarian positions dollar-neutral within each sector.
    """

    BOT_NAME = "ts151_MeanReversionClusterBot"
    DEFAULT_UNIVERSE = "sp500"
    BENCHMARKS = ()
    ALLOW_SHORT = True
    LONG_ONLY = False
    MAX_GROSS = 2.0
    BACKTEST_PERIOD = "max"
    LIVE_PERIOD = "3y"
    MIN_UNIVERSE_COVERAGE = 0.8

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "window": 5,
        "gross": 2.0,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "window": [3, 5, 10],
        "gross": [1.0, 2.0],
    }

    def __init__(
        self,
        *,
        interval: str = "1d",
        period: str | None = None,
        rebalance: str = "D",
        universe: str | tuple[str, ...] | list[str] | None = None,
        long_only: bool | None = None,
        park_cash: bool | None = None,
        window: int = 5,
        gross: float = 2.0,
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
            gross=gross,
            **params,
        )

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return compute_mean_reversion_cluster_weights(
            ctx.closes,
            window=int(self.window),
            gross=float(self.MAX_GROSS),
        )


if __name__ == "__main__":
    run_bot(TS151MeanReversionClusterBot)
