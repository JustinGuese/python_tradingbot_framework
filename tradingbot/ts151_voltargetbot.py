"""Kakushadze & Serur (2018) §6.5: Volatility Targeting strategy bot.

Scales equity exposure to maintain constant target portfolio volatility:
    Weight = min(cap=1.0, target=0.15 / (realized_vol(window=21) * sqrt(252)))
Rebalances only when the change exceeds band=0.1.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_volfx import vol_target_signal


class TS151VolTargetBot(TS151Bot):
    """Volatility Targeting strategy bot (§6.5).

    Scales SPY index exposure inversely with realized volatility to target constant vol,
    rebalancing only when target weight changes by more than the rebalance band.
    """

    BOT_NAME = "ts151_VolTargetBot"
    DEFAULT_UNIVERSE = ("SPY",)
    BENCHMARKS = ()
    ALLOW_SHORT = False
    LONG_ONLY = True
    MAX_GROSS = 1.0
    BACKTEST_PERIOD = "max"
    LIVE_PERIOD = "3y"
    MIN_UNIVERSE_COVERAGE = 1.0

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "target": 0.15,
        "window": 21,
        "cap": 1.0,
        "band": 0.1,
        "annualization_factor": 252.0,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "target": [0.10, 0.15, 0.20],
        "window": [15, 21, 42],
        "band": [0.05, 0.10, 0.15],
        "cap": [1.0],
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
        target: float = 0.15,
        window: int = 21,
        cap: float = 1.0,
        band: float = 0.1,
        annualization_factor: float = 252.0,
        **params: Any,
    ):
        super().__init__(
            interval=interval,
            period=period,
            rebalance=rebalance,
            universe=universe,
            long_only=long_only,
            park_cash=park_cash,
            target=target,
            window=window,
            cap=cap,
            band=band,
            annualization_factor=annualization_factor,
            **params,
        )

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return vol_target_signal(
            ctx,
            target=float(self.target),
            window=int(self.window),
            cap=float(self.cap),
            band=float(self.band),
            annualization_factor=float(self.annualization_factor),
        )
