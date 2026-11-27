"""Kakushadze & Serur (2018) §7.6: Replicating Variance Swap / VRP strategy bot.

Monitors volatility risk premium:
    Spread = (^VIX / 100)^2 - RV^2(SPY, window=21)
    If spread > entry=0.01 -> short VIXY sized by spread / scale=0.04 (capped 0.5)
    If spread < 0.0 -> flat
"""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_volfx import variance_swap_signal


class TS151VarianceSwapBot(TS151Bot):
    """Replicating Variance Swap / VRP strategy bot (§7.6).

    Extracts the Volatility Risk Premium by shorting volatility when implied variance exceeds
    realized variance by an entry threshold.
    """

    BOT_NAME = "ts151_VarianceSwapBot"
    DEFAULT_UNIVERSE = ("VIXY", "SVXY")
    BENCHMARKS = ("^VIX", "SPY")
    ALLOW_SHORT = True
    MAX_GROSS = 1.0
    BACKTEST_PERIOD = "max"
    LIVE_PERIOD = "3y"
    MIN_UNIVERSE_COVERAGE = 1.0

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "entry": 0.01,
        "exit": 0.0,
        "scale": 0.04,
        "cap": 0.5,
        "window": 21,
        "vix_ticker": "^VIX",
        "spy_ticker": "SPY",
        "vixy_ticker": "VIXY",
        "inverse_ticker": "SVXY",
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "entry": [0.005, 0.01, 0.02],
        "scale": [0.02, 0.04, 0.06],
        "cap": [0.3, 0.5, 0.7],
        "window": [15, 21, 42],
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
        entry: float = 0.01,
        exit: float = 0.0,
        scale: float = 0.04,
        cap: float = 0.5,
        window: int = 21,
        vix_ticker: str = "^VIX",
        spy_ticker: str = "SPY",
        vixy_ticker: str = "VIXY",
        inverse_ticker: str = "SVXY",
        **params: Any,
    ):
        super().__init__(
            interval=interval,
            period=period,
            rebalance=rebalance,
            universe=universe,
            long_only=long_only,
            park_cash=park_cash,
            entry=entry,
            exit=exit,
            scale=scale,
            cap=cap,
            window=window,
            vix_ticker=vix_ticker,
            spy_ticker=spy_ticker,
            vixy_ticker=vixy_ticker,
            inverse_ticker=inverse_ticker,
            **params,
        )

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return variance_swap_signal(
            ctx,
            vix_ticker=str(self.vix_ticker),
            spy_ticker=str(self.spy_ticker),
            vixy_ticker=str(self.vixy_ticker),
            entry=float(self.entry),
            exit=float(self.exit),
            scale=float(self.scale),
            cap=float(self.cap),
            window=int(self.window),
            long_only=bool(self.long_only),
            inverse_ticker=str(self.inverse_ticker),
        )
