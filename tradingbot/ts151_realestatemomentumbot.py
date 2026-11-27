"""TS151 Real Estate Momentum Bot (§16.4)."""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_macro import real_estate_momentum_weights


class TS151RealEstateMomentumBot(TS151Bot):
    """Real estate multi-horizon cross-sectional momentum (§16.4).

    Scores REITs by the average of returns over 21, 63, 126, and 252 bars,
    longing the top k names.
    """

    BOT_NAME = "ts151_realestatemomentumbot"
    DEFAULT_UNIVERSE = "reits"
    ALLOW_SHORT = False
    LONG_ONLY = True
    MAX_GROSS = 1.0

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "lookbacks": (21, 63, 126, 252),
        "k": 2,
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "lookbacks": [
            (21, 63, 126, 252),
            (63, 126, 252),
            (21, 63),
        ],
        "k": [1, 2, 3],
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
        return real_estate_momentum_weights(
            closes=ctx.closes,
            lookbacks=self.lookbacks,
            k=self.k,
            gross=float(self.MAX_GROSS),
        )
