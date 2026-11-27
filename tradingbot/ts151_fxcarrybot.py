"""Kakushadze & Serur (2018) §8.2: FX Carry Trade strategy bot.

Ranks FX ETFs by short-rate differential vs USD:
    Diff_i = r_i - r_USD (from FRED 3-month short interest rates)
Long top k=2 (highest yielders), short bottom k=2 (lowest yielders).
Monthly rebalancing, dollar-neutral, gross leverage = 1.0.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_volfx import fx_carry_signal


class TS151FxCarryBot(TS151Bot):
    """FX Carry Trade strategy bot (§8.2).

    Exploits Uncovered Interest Rate Parity violations across G10 currencies.
    """

    BOT_NAME = "ts151_FxCarryBot"
    DEFAULT_UNIVERSE = "fx_etfs"
    BENCHMARKS = ()
    ALLOW_SHORT = True
    NEEDS_NETWORK = True
    MAX_GROSS = 1.0
    BACKTEST_PERIOD = "max"
    LIVE_PERIOD = "3y"
    MIN_UNIVERSE_COVERAGE = 1.0

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "k": 2,
        "gross": 1.0,
        "lag_days": 25,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "k": [1, 2, 3],
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
        k: int = 2,
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
            k=k,
            gross=gross,
            lag_days=lag_days,
            **params,
        )

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return fx_carry_signal(
            ctx,
            k=int(self.k),
            gross=float(self.gross),
            lag_days=int(self.lag_days),
        )
