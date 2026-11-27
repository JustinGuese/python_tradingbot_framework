"""Kakushadze & Serur (2018) §3.7: Residual Momentum.

Residual 12-1 momentum from 36-month OLS regression on SPY.
Long top `frac=0.1`, short bottom `frac=0.1`.
Defaults: lookback_bars=756, momentum_bars=252, skip_bars=21, frac=0.1, gross=2.0.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.runner import run_bot
from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_stocks import compute_residual_momentum_weights


class TS151ResidualMomentumBot(TS151Bot):
    """Residual Momentum strategy (§3.7).

    Evaluates idiosyncratic stock momentum by stripping out systematic market (SPY) exposure
    via rolling OLS regression. Longs top residual return, shorts bottom.
    """

    BOT_NAME = "ts151_ResidualMomentumBot"
    DEFAULT_UNIVERSE = "sp500"
    BENCHMARKS = ("SPY",)
    ALLOW_SHORT = True
    LONG_ONLY = False
    MAX_GROSS = 2.0
    BACKTEST_PERIOD = "max"
    LIVE_PERIOD = "5y"
    MIN_UNIVERSE_COVERAGE = 0.8

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "lookback_bars": 756,
        "momentum_bars": 252,
        "skip_bars": 21,
        "frac": 0.1,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "lookback_bars": [504, 756],
        "momentum_bars": [126, 252],
        "skip_bars": [0, 21],
        "frac": [0.05, 0.1, 0.2],
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
        lookback_bars: int = 756,
        momentum_bars: int = 252,
        skip_bars: int = 21,
        frac: float = 0.1,
        **params: Any,
    ):
        super().__init__(
            interval=interval,
            period=period,
            rebalance=rebalance,
            universe=universe,
            long_only=long_only,
            park_cash=park_cash,
            lookback_bars=lookback_bars,
            momentum_bars=momentum_bars,
            skip_bars=skip_bars,
            frac=frac,
            **params,
        )

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return compute_residual_momentum_weights(
            ctx.closes,
            benchmark_ticker="SPY",
            lookback_bars=int(self.lookback_bars),
            momentum_bars=int(self.momentum_bars),
            skip_bars=int(self.skip_bars),
            frac=float(self.frac),
            long_only=self.long_only,
            gross=float(self.MAX_GROSS),
        )


if __name__ == "__main__":
    run_bot(TS151ResidualMomentumBot)
