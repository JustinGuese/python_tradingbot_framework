"""Kakushadze & Serur (2018) §8.1: FX Moving Average crossover strategy bot.

Trend-following across G10 FX ETFs:
    For each ETF: SMA(fast=50) > SMA(slow=200) -> long (+1), else short (-1).
Equal-weighted across the universe with gross leverage = 1.0. Rebalanced daily.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_volfx import fx_ma_signal


class TS151FxMaBot(TS151Bot):
    """FX Moving Average crossover strategy bot (§8.1).

    Classic dual moving average trend-following strategy evaluated across liquid currency ETFs.
    """

    BOT_NAME = "ts151_FxMaBot"
    DEFAULT_UNIVERSE = "fx_etfs"
    BENCHMARKS = ()
    ALLOW_SHORT = True
    MAX_GROSS = 1.0
    BACKTEST_PERIOD = "max"
    LIVE_PERIOD = "3y"
    MIN_UNIVERSE_COVERAGE = 1.0

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "fast": 50,
        "slow": 200,
        "gross": 1.0,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "fast": [20, 50, 100],
        "slow": [100, 150, 200],
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
        gross: float = 1.0,
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
            gross=gross,
            **params,
        )

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return fx_ma_signal(
            ctx,
            fast=int(self.fast),
            slow=int(self.slow),
            gross=float(self.gross),
        )
