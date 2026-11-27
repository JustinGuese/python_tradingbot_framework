"""Kakushadze & Serur (2018) §3.4: Low Volatility.

Vol = std of daily returns over `window=252` bars.
Long lowest `frac=0.1`, short highest `frac=0.1`.
Defaults: window=252, frac=0.1, gross=2.0.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.runner import run_bot
from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_stocks import compute_low_volatility_weights


class TS151LowVolatilityBot(TS151Bot):
    """Low Volatility strategy (§3.4).

    Ranks stocks by historical realized return volatility, holding long the lowest volatility
    stocks and short the highest volatility stocks.
    """

    BOT_NAME = "ts151_LowVolatilityBot"
    DEFAULT_UNIVERSE = "sp500"
    BENCHMARKS = ()
    ALLOW_SHORT = True
    LONG_ONLY = False
    MAX_GROSS = 2.0
    BACKTEST_PERIOD = "max"
    LIVE_PERIOD = "3y"
    MIN_UNIVERSE_COVERAGE = 0.8

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "window": 252,
        "frac": 0.1,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "window": [126, 252, 504],
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
        window: int = 252,
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
            window=window,
            frac=frac,
            **params,
        )

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return compute_low_volatility_weights(
            ctx.closes,
            window=int(self.window),
            frac=float(self.frac),
            long_only=self.long_only,
            gross=float(self.MAX_GROSS),
        )


if __name__ == "__main__":
    run_bot(TS151LowVolatilityBot)
