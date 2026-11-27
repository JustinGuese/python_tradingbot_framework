"""Kakushadze & Serur (2018) §3.12: Two Moving Averages.

SMA(fast=50) > SMA(slow=200) long, else short/flat.
Optional stop_loss=0.0 (off).
Universe: SPY. Rebalance: D.
Defaults: fast=50, slow=200, stop_loss=0.0, gross=1.0.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.runner import run_bot
from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_stocks import compute_two_ma_weights


class TS151TwoMABot(TS151Bot):
    """Two Moving Averages strategy (§3.12).

    Classic golden cross / death cross moving average crossover strategy on SPY.
    """

    BOT_NAME = "ts151_TwoMABot"
    DEFAULT_UNIVERSE = ("SPY",)
    BENCHMARKS = ()
    ALLOW_SHORT = True
    LONG_ONLY = False
    MAX_GROSS = 1.0
    BACKTEST_PERIOD = "max"
    LIVE_PERIOD = "3y"
    MIN_UNIVERSE_COVERAGE = 1.0

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "fast": 50,
        "slow": 200,
        "stop_loss": 0.0,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "fast": [20, 50],
        "slow": [100, 200],
        "stop_loss": [0.0, 0.05],
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
        fast: int = 50,
        slow: int = 200,
        stop_loss: float = 0.0,
        **params: Any,
    ):
        super().__init__(
            interval=interval,
            period=period,
            rebalance=rebalance,
            universe=universe,
            long_only=long_only,
            park_cash=park_cash,
            fast=fast,
            slow=slow,
            stop_loss=stop_loss,
            **params,
        )

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        ticker = self.universe_list[0] if self.universe_list else "SPY"
        return compute_two_ma_weights(
            ctx.closes,
            ticker=ticker,
            fast=int(self.fast),
            slow=int(self.slow),
            stop_loss=float(self.stop_loss),
            long_only=self.long_only,
            gross=float(self.MAX_GROSS),
        )


if __name__ == "__main__":
    run_bot(TS151TwoMABot)
