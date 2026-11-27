"""Kakushadze & Serur (2018) §3.17: K-Nearest Neighbors (KNN).

Per sector ETF, trains a sklearn KNeighborsClassifier(n_neighbors=k=10) on last train=504 bars.
Features: returns over 1/5/21 bars and close/SMA(21), close/SMA(63).
Label = sign of forward horizon=5 return.
Never train on labels ending after as_of.
Universe: sectors. Rebalance: W.
Defaults: k=10, train=504, horizon=5, gross=1.0.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.runner import run_bot
from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_stocks import compute_knn_weights


class TS151KNNBot(TS151Bot):
    """K-Nearest Neighbors strategy (§3.17).

    Non-parametric machine learning classifier applied to technical features and momentum indicators.
    Trains on trailing history without forward leakage and goes long predicted positive return assets.
    """

    BOT_NAME = "ts151_KNNBot"
    DEFAULT_UNIVERSE = "sectors"
    BENCHMARKS = ()
    ALLOW_SHORT = False
    LONG_ONLY = True
    MAX_GROSS = 1.0
    BACKTEST_PERIOD = "max"
    LIVE_PERIOD = "5y"
    MIN_UNIVERSE_COVERAGE = 1.0

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "k": 10,
        "train": 504,
        "horizon": 5,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "k": [5, 10, 20],
        "train": [252, 504],
        "horizon": [3, 5],
    }

    def __init__(
        self,
        *,
        interval: str = "1d",
        period: str | None = None,
        rebalance: str = "W",
        universe: str | tuple[str, ...] | list[str] | None = None,
        long_only: bool | None = None,
        park_cash: bool | None = None,
        k: int = 10,
        train: int = 504,
        horizon: int = 5,
        **params: Any,
    ):
        super().__init__(
            interval=interval,
            period=period,
            rebalance=rebalance,
            universe=universe,
            long_only=long_only,
            park_cash=park_cash,
            k=k,
            train=train,
            horizon=horizon,
            **params,
        )

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return compute_knn_weights(
            ctx.closes,
            universe=ctx.universe,
            as_of=ctx.as_of,
            k=int(self.k),
            train=int(self.train),
            horizon=int(self.horizon),
        )


if __name__ == "__main__":
    run_bot(TS151KNNBot)
