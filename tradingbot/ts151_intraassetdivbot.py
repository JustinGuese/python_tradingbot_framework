"""TS151 Intra-Asset Diversification Bot (§16.3)."""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_macro import intra_asset_div_weights


class TS151IntraAssetDivBot(TS151Bot):
    """Equal Risk Contribution (ERC) diversification within real estate REITs (§16.3).

    Allocates risk equally across the REIT universe using risk parity.
    """

    BOT_NAME = "ts151_intraassetdivbot"
    DEFAULT_UNIVERSE = "reits"
    ALLOW_SHORT = False
    LONG_ONLY = True
    MAX_GROSS = 1.0

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "window": 252,
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "window": [63, 126, 252, 504],
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
        return intra_asset_div_weights(
            closes=ctx.closes,
            window=self.window,
        )
