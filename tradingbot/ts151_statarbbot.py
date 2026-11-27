"""Kakushadze & Serur (2018) §3.18: Statistical Arbitrage.

PCA of 60-bar returns (n_factors=5). Residual s-score per Avellaneda-Lee over window=60.
Short when s > entry=1.25, long when s < -1.25, exit when abs(s) < exit=0.5.
Weights equal, dollar-neutral.
Universe: sp100 by default (PCA cost). Rebalance: D.
Defaults: n_factors=5, window=60, entry=1.25, exit=0.5, gross=2.0.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.runner import run_bot
from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_stocks import compute_statarb_weights


class TS151StatArbBot(TS151Bot):
    """Statistical Arbitrage strategy (§3.18).

    Avellaneda & Lee (2010) PCA eigenportfolio residual mean-reversion with OU process modeling.
    """

    BOT_NAME = "ts151_StatArbBot"
    DEFAULT_UNIVERSE = "sp100"
    BENCHMARKS = ()
    ALLOW_SHORT = True
    LONG_ONLY = False
    MAX_GROSS = 2.0
    BACKTEST_PERIOD = "max"
    LIVE_PERIOD = "5y"
    MIN_UNIVERSE_COVERAGE = 0.8

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "n_factors": 5,
        "window": 60,
        "entry": 1.25,
        "exit": 0.5,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "n_factors": [3, 5],
        "window": [40, 60],
        "entry": [1.0, 1.25, 1.5],
        "exit": [0.25, 0.5],
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
        n_factors: int = 5,
        window: int = 60,
        entry: float = 1.25,
        exit: float = 0.5,
        **params: Any,
    ):
        super().__init__(
            interval=interval,
            period=period,
            rebalance=rebalance,
            universe=universe,
            long_only=long_only,
            park_cash=park_cash,
            n_factors=n_factors,
            window=window,
            entry=entry,
            exit=exit,
            **params,
        )

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return compute_statarb_weights(
            ctx.closes,
            n_factors=int(self.n_factors),
            window=int(self.window),
            entry=float(self.entry),
            exit_val=float(self.exit),
            gross=float(self.MAX_GROSS),
        )


if __name__ == "__main__":
    run_bot(TS151StatArbBot)
