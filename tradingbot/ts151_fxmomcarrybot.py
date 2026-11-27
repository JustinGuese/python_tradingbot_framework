"""Kakushadze & Serur (2018) §8.4: FX Momentum + Carry Blend strategy bot.

Blends cross-sectional price momentum and interest rate carry:
    Score = w_mom * z(return over window=63) + (1 - w_mom) * z(carry)
Long top k=2, short bottom k=2.
Monthly rebalancing, dollar-neutral, gross leverage = 1.0.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_volfx import fx_mom_carry_signal


class TS151FxMomCarryBot(TS151Bot):
    """FX Momentum + Carry Blend strategy bot (§8.4).

    Combines currency trend momentum and interest carry yield into a diversified multi-factor signal.
    """

    BOT_NAME = "ts151_FxMomCarryBot"
    DEFAULT_UNIVERSE = "fx_etfs"
    BENCHMARKS = ()
    ALLOW_SHORT = True
    NEEDS_NETWORK = True
    MAX_GROSS = 1.0
    BACKTEST_PERIOD = "max"
    LIVE_PERIOD = "3y"
    MIN_UNIVERSE_COVERAGE = 1.0

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "window": 63,
        "w_mom": 0.5,
        "k": 2,
        "gross": 1.0,
        "lag_days": 25,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "w_mom": [0.3, 0.5, 0.7],
        "window": [42, 63, 126],
        "k": [1, 2, 3],
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
        window: int = 63,
        w_mom: float = 0.5,
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
            window=window,
            w_mom=w_mom,
            k=k,
            gross=gross,
            lag_days=lag_days,
            **params,
        )

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return fx_mom_carry_signal(
            ctx,
            window=int(self.window),
            w_mom=float(self.w_mom),
            k=int(self.k),
            gross=float(self.gross),
            lag_days=int(self.lag_days),
        )
