"""Kakushadze & Serur (2018) §3.14: Support and Resistance (Pivot Points).

Pivot C from previous bar. Long if close > C, exit at R. Short if close < C, exit at S.
Universe: SPY. Rebalance: D.
Defaults: hold_bars=1, gross=1.0.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.runner import run_bot
from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_stocks import compute_support_resistance_weights


class TS151SupportResistanceBot(TS151Bot):
    """Support and Resistance strategy (§3.14).

    Floor trader pivot points: Center (C), Resistance (R), and Support (S).
    Trades breakouts relative to pivot levels.
    """

    BOT_NAME = "ts151_SupportResistanceBot"
    DEFAULT_UNIVERSE = ("SPY",)
    BENCHMARKS = ()
    ALLOW_SHORT = True
    LONG_ONLY = False
    MAX_GROSS = 1.0
    BACKTEST_PERIOD = "max"
    LIVE_PERIOD = "3y"
    MIN_UNIVERSE_COVERAGE = 1.0

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "hold_bars": 1,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "hold_bars": [1, 2],
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
        hold_bars: int = 1,
        **params: Any,
    ):
        super().__init__(
            interval=interval,
            period=period,
            rebalance=rebalance,
            universe=universe,
            long_only=long_only,
            park_cash=park_cash,
            hold_bars=hold_bars,
            **params,
        )

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        ticker = self.universe_list[0] if self.universe_list else "SPY"
        return compute_support_resistance_weights(
            ctx.datas,
            ticker=ticker,
            long_only=self.long_only,
            gross=float(self.MAX_GROSS),
        )


if __name__ == "__main__":
    run_bot(TS151SupportResistanceBot)
