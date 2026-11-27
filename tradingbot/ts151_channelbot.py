"""Kakushadze & Serur (2018) §3.15: Channel (Donchian Mean Reversion).

Donchian channel over `window=20`.
Buy at the channel low (close <= min), sell/short at the channel high (mean reversion).
Universe: SPY. Rebalance: D.
Defaults: window=20, gross=1.0.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.runner import run_bot
from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_stocks import compute_channel_weights


class TS151ChannelBot(TS151Bot):
    """Channel Mean Reversion strategy (§3.15).

    Trades mean-reversion at Donchian channel envelope boundaries.
    """

    BOT_NAME = "ts151_ChannelBot"
    DEFAULT_UNIVERSE = ("SPY",)
    BENCHMARKS = ()
    ALLOW_SHORT = True
    LONG_ONLY = False
    MAX_GROSS = 1.0
    BACKTEST_PERIOD = "max"
    LIVE_PERIOD = "3y"
    MIN_UNIVERSE_COVERAGE = 1.0

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "window": 20,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "window": [10, 20, 40],
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
        window: int = 20,
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
        return compute_channel_weights(
            ctx.closes,
            ticker=ticker,
            window=int(self.window),
            long_only=self.long_only,
            gross=float(self.MAX_GROSS),
        )


if __name__ == "__main__":
    run_bot(TS151ChannelBot)
