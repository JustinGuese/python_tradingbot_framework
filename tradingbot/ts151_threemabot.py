"""Kakushadze & Serur (2018) §3.13: Three Moving Averages.

Long if SMA(s1=3) > SMA(s2=10) > SMA(s3=21), short if SMA(s1) < SMA(s2) < SMA(s3),
else keep the current state via pure state replay.
Universe: SPY. Rebalance: D.
Defaults: s1=3, s2=10, s3=21, gross=1.0.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.runner import run_bot
from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_stocks import compute_three_ma_weights


class TS151ThreeMABot(TS151Bot):
    """Three Moving Averages strategy (§3.13).

    Multi-horizon trend alignment system using state replay.
    """

    BOT_NAME = "ts151_ThreeMABot"
    DEFAULT_UNIVERSE = ("SPY",)
    BENCHMARKS = ()
    ALLOW_SHORT = True
    LONG_ONLY = False
    MAX_GROSS = 1.0
    BACKTEST_PERIOD = "max"
    LIVE_PERIOD = "3y"
    MIN_UNIVERSE_COVERAGE = 1.0

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "s1": 3,
        "s2": 10,
        "s3": 21,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "s1": [3, 5],
        "s2": [10, 15],
        "s3": [21, 30],
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
        s1: int = 3,
        s2: int = 10,
        s3: int = 21,
        **params: Any,
    ):
        super().__init__(
            interval=interval,
            period=period,
            rebalance=rebalance,
            universe=universe,
            long_only=long_only,
            park_cash=park_cash,
            s1=s1,
            s2=s2,
            s3=s3,
            **params,
        )

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        ticker = self.universe_list[0] if self.universe_list else "SPY"
        return compute_three_ma_weights(
            ctx.closes,
            ticker=ticker,
            s1=int(self.s1),
            s2=int(self.s2),
            s3=int(self.s3),
            long_only=self.long_only,
            gross=float(self.MAX_GROSS),
        )


if __name__ == "__main__":
    run_bot(TS151ThreeMABot)
