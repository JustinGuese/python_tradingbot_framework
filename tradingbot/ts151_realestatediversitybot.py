"""TS151 Real Estate Diversity Bot (§16.2)."""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_macro import real_estate_diversity_weights


class TS151RealEstateDiversityBot(TS151Bot):
    """Mean-variance portfolio diversification across REITs with risk cap (§16.2).

    Minimizes portfolio variance subject to min and max weight constraints.
    If annualized portfolio volatility exceeds max_vol, scales down equity exposure to cash.
    """

    BOT_NAME = "ts151_realestatediversitybot"
    DEFAULT_UNIVERSE = "reits"
    ALLOW_SHORT = False
    LONG_ONLY = True
    MAX_GROSS = 1.0

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "window": 252,
        "min_weight": 0.05,
        "max_weight": 0.5,
        "max_vol": 0.15,
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "window": [126, 252, 504],
        "min_weight": [0.0, 0.05, 0.1],
        "max_weight": [0.3, 0.5, 0.7],
        "max_vol": [0.10, 0.15, 0.20],
    }

    def __init__(
        self,
        *,
        interval: str = "1d",
        period: str | None = None,
        rebalance: str = "M",
        universe: str | tuple[str, ...] | list[str] | None = None,
        long_only: bool = True,
        park_cash: bool = False,
        **params: Any,
    ):
        super().__init__(
            interval=interval,
            period=period,
            rebalance=rebalance,
            universe=universe,
            long_only=long_only,
            park_cash=park_cash,
            **params,
        )

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return real_estate_diversity_weights(
            closes=ctx.closes,
            window=self.window,
            min_weight=self.min_weight,
            max_weight=self.max_weight,
            max_vol=self.max_vol,
        )
