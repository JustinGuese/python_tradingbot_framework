"""Kakushadze & Serur (2018) §4.4: Internal Bar Strength (IBS) bot.

Per ETF: enters long when IBS < low (0.2), exits when IBS > high (0.8),
otherwise holds state. Equal weight across held ETFs, evaluated daily.

Schedule Tradeoff Note:
Live CronJob schedule is '50 20 * * 1-5' (20:50 UTC weekdays). The winter NYSE
cash close is 21:00 UTC. IBS requires the daily bar high, low, close; running at
20:50 UTC samples the bar 10 minutes prior to final market close to enable same-day
execution while accepting minimal tracking variance to the official closing tick.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_etfs import ibs_reversal


class TS151IBSBot(TS151Bot):
    """Internal Bar Strength (IBS) mean-reversion bot (§4.4).

    Replays hysteresis state (enter < low, exit > high) on internal bar strength
    (close - low) / (high - low) across universe members, equal weighting held assets.
    """

    BOT_NAME = "ts151_IBSBot"
    DEFAULT_UNIVERSE = ("SPY", "QQQ")
    BENCHMARKS = ()
    ALLOW_SHORT = False
    LONG_ONLY = True
    MAX_GROSS = 1.0
    BACKTEST_PERIOD = "max"
    LIVE_PERIOD = "3y"
    MIN_UNIVERSE_COVERAGE = 1.0

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "low": 0.2,
        "high": 0.8,
        "gross": 1.0,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "low": [0.15, 0.2, 0.25],
        "high": [0.75, 0.8, 0.85],
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
        low: float = 0.2,
        high: float = 0.8,
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
            low=low,
            high=high,
            gross=gross,
            **params,
        )

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return ibs_reversal(
            ctx,
            low=float(self.low),
            high=float(self.high),
            gross=float(self.gross),
        )
