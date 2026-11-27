"""Kakushadze & Serur (2018) §8.3: Dollar Carry Trade strategy bot.

Evaluates average currency interest rate differential against the US dollar:
    Avg_Diff = mean(r_foreign - r_USD)
If Avg_Diff > 0 -> long all foreign currency ETFs (short USD)
If Avg_Diff <= 0 -> short all foreign currency ETFs (long USD)
Equal-weighted across the universe with monthly rebalancing, gross leverage = 1.0.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_volfx import dollar_carry_signal


class TS151DollarCarryBot(TS151Bot):
    """Dollar Carry Trade strategy bot (§8.3).

    Aggregates the global interest rate differential against the USD (Lustig et al.)
    to tilt long or short the dollar basket.
    """

    BOT_NAME = "ts151_DollarCarryBot"
    DEFAULT_UNIVERSE = "fx_etfs"
    BENCHMARKS = ()
    ALLOW_SHORT = True
    NEEDS_NETWORK = True
    MAX_GROSS = 1.0
    BACKTEST_PERIOD = "max"
    LIVE_PERIOD = "3y"
    MIN_UNIVERSE_COVERAGE = 1.0

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "gross": 1.0,
        "lag_days": 25,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "lag_days": [15, 25, 35],
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
        gross: float = 1.0,
        lag_days: int = 25,
        **params: Any,
    ):
        super().__init__(
            interval=interval,
            period=period,
            rebalance=rebalance,
            universe=universe,
            long_only=long_only,
            park_cash=park_cash,
            gross=gross,
            lag_days=lag_days,
            **params,
        )

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return dollar_carry_signal(
            ctx,
            gross=float(self.gross),
            lag_days=int(self.lag_days),
        )
