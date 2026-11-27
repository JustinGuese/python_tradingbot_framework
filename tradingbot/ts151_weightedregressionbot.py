"""Kakushadze & Serur (2018) §3.10: Weighted Regression Mean Reversion.

Cross-sectional WLS of `window=5` returns on sector dummies with weights 1/σ² (σ over vol_window=60).
Weights ∝ -residual/σ², demeaned cross-sectionally.
Defaults: window=5, vol_window=60, gross=2.0.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.runner import run_bot
from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_stocks import compute_weighted_regression_weights


class TS151WeightedRegressionBot(TS151Bot):
    """Weighted Regression Mean Reversion (§3.10).

    Cross-sectional weighted least squares (WLS) regression of short-term returns on sector dummies
    weighted by inverse volatility variance. Reverses residual deviations.
    """

    BOT_NAME = "ts151_WeightedRegressionBot"
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
        "vol_window": 60,
        "gross": 2.0,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "window": [3, 5, 10],
        "vol_window": [30, 60, 90],
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
        vol_window: int = 60,
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
            vol_window=vol_window,
            gross=gross,
            **params,
        )

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return compute_weighted_regression_weights(
            ctx.closes,
            window=int(self.window),
            vol_window=int(self.vol_window),
            gross=float(self.MAX_GROSS),
        )


if __name__ == "__main__":
    run_bot(TS151WeightedRegressionBot)
