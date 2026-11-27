"""Kakushadze & Serur (2018) §7.3: Hedged Volatility Carry strategy bot.

Shorts VIXY at weight=0.3 plus a short SPY hedge:
    Hedge weight on SPY = -beta * weight (long SPY when beta < 0, where beta is
    the rolling OLS slope of VIXY on SPY over window=63 bars).
Active only when VIX term structure is in contango (^VIX / ^VIX3M < 0.95).
"""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_volfx import vol_carry_signal


class TS151VolCarryBot(TS151Bot):
    """Volatility Carry Trade strategy bot (§7.3).

    Captures volatility contango roll yield while hedging equity market beta.
    """

    BOT_NAME = "ts151_VolCarryBot"
    DEFAULT_UNIVERSE = ("VIXY", "SPY")
    BENCHMARKS = ("^VIX", "^VIX3M")
    ALLOW_SHORT = True
    MAX_GROSS = 2.0
    BACKTEST_PERIOD = "max"
    LIVE_PERIOD = "3y"
    MIN_UNIVERSE_COVERAGE = 1.0

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "contango": 0.95,
        "weight": 0.3,
        "window": 63,
        "max_hedge": 1.0,
        "vix_ticker": "^VIX",
        "vix3m_ticker": "^VIX3M",
        "vixy_ticker": "VIXY",
        "spy_ticker": "SPY",
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "contango": [0.90, 0.95, 0.98],
        "weight": [0.2, 0.3, 0.4],
        "window": [42, 63, 126],
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
        contango: float = 0.95,
        weight: float = 0.3,
        window: int = 63,
        max_hedge: float = 1.0,
        vix_ticker: str = "^VIX",
        vix3m_ticker: str = "^VIX3M",
        vixy_ticker: str = "VIXY",
        spy_ticker: str = "SPY",
        **params: Any,
    ):
        super().__init__(
            interval=interval,
            period=period,
            rebalance=rebalance,
            universe=universe,
            long_only=long_only,
            park_cash=park_cash,
            contango=contango,
            weight=weight,
            window=window,
            max_hedge=max_hedge,
            vix_ticker=vix_ticker,
            vix3m_ticker=vix3m_ticker,
            vixy_ticker=vixy_ticker,
            spy_ticker=spy_ticker,
            **params,
        )

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return vol_carry_signal(
            ctx,
            vix_ticker=str(self.vix_ticker),
            vix3m_ticker=str(self.vix3m_ticker),
            vixy_ticker=str(self.vixy_ticker),
            spy_ticker=str(self.spy_ticker),
            contango=float(self.contango),
            weight=float(self.weight),
            window=int(self.window),
            max_hedge=float(self.max_hedge),
        )
