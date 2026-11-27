"""Kakushadze & Serur (2018) §3.11: Single Moving Average.

Long when close > SMA(window=200), else short (LS) or flat (long_only).
Universe: SPY. Rebalance: D.
Defaults: window=200, gross=1.0.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.runner import run_bot
from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_stocks import compute_single_ma_weights


class TS151SingleMABot(TS151Bot):
    """Single Moving Average strategy (§3.11).

    Trend-following strategy on an equity index (SPY).
    Long when close is above SMA(window), short (or flat) when below.
    """

    BOT_NAME = "ts151_SingleMABot"
    DEFAULT_UNIVERSE = ("SPY",)
    BENCHMARKS = ()
    ALLOW_SHORT = True
    LONG_ONLY = False
    MAX_GROSS = 1.0
    BACKTEST_PERIOD = "max"
    LIVE_PERIOD = "3y"
    MIN_UNIVERSE_COVERAGE = 1.0

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "window": 200,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "window": [50, 100, 200],
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
        window: int = 200,
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
            **params,
        )

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        ticker = self.universe_list[0] if self.universe_list else "SPY"
        return compute_single_ma_weights(
            ctx.closes,
            ticker=ticker,
            window=int(self.window),
            long_only=self.long_only,
            gross=float(self.MAX_GROSS),
        )


if __name__ == "__main__":
    run_bot(TS151SingleMABot)
