"""Kakushadze & Serur (2018) §4.3: R-squared Rotation strategy bot.

Filters sector ETFs to the fraction with lowest R^2 against SPY over `window` bars.
Among those, holds the top `k` names by Jensen's alpha, rebalanced monthly.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_etfs import rsquared_rotation


class TS151RSquaredBot(TS151Bot):
    """R-squared Rotation strategy bot (§4.3).

    Filters universe to the `frac` with lowest R^2 vs benchmark SPY over `window`
    bars, then selects top `k` by Jensen's alpha.
    """

    BOT_NAME = "ts151_RSquaredBot"
    DEFAULT_UNIVERSE = "sectors"
    BENCHMARKS = ("SPY",)
    ALLOW_SHORT = False
    LONG_ONLY = True
    MAX_GROSS = 1.0
    BACKTEST_PERIOD = "max"
    LIVE_PERIOD = "3y"
    MIN_UNIVERSE_COVERAGE = 1.0

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "window": 126,
        "frac": 0.5,
        "k": 3,
        "benchmark": "SPY",
        "gross": 1.0,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "window": [63, 126, 252],
        "frac": [0.33, 0.5, 0.67],
        "k": [2, 3],
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
        window: int = 126,
        frac: float = 0.5,
        k: int = 3,
        benchmark: str = "SPY",
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
            window=window,
            frac=frac,
            k=k,
            benchmark=benchmark,
            gross=gross,
            **params,
        )

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return rsquared_rotation(
            ctx,
            window=int(self.window),
            frac=float(self.frac),
            k=int(self.k),
            benchmark=str(self.benchmark),
            gross=float(self.gross),
        )
