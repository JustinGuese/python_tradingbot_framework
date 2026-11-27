"""Kakushadze & Serur (2018) §7.2: VIX Basis / Term Structure strategy bot.

Trades volatility ETF based on VIX term structure slope (^VIX / ^VIX3M):
    If ^VIX / ^VIX3M < contango=0.95 -> short VIXY at weight=0.3
    If ^VIX / ^VIX3M > backwardation=1.05 -> long VIXY at weight=0.3
    Otherwise -> flat
Long-only mode maps short VIXY to long SVXY.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_volfx import vix_basis_signal


class TS151VixBasisBot(TS151Bot):
    """VIX Basis / Term Structure strategy bot (§7.2).

    Exploits roll yield in volatility futures via ^VIX / ^VIX3M term structure contango/backwardation.
    """

    BOT_NAME = "ts151_VixBasisBot"
    DEFAULT_UNIVERSE = ("VIXY", "SVXY")
    BENCHMARKS = ("^VIX", "^VIX3M")
    ALLOW_SHORT = True
    MAX_GROSS = 1.0
    BACKTEST_PERIOD = "max"
    LIVE_PERIOD = "3y"
    MIN_UNIVERSE_COVERAGE = 1.0

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "contango": 0.95,
        "backwardation": 1.05,
        "weight": 0.3,
        "vix_ticker": "^VIX",
        "vix3m_ticker": "^VIX3M",
        "trade_ticker": "VIXY",
        "inverse_ticker": "SVXY",
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "contango": [0.90, 0.95, 0.98],
        "backwardation": [1.02, 1.05, 1.10],
        "weight": [0.2, 0.3, 0.5],
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
        backwardation: float = 1.05,
        weight: float = 0.3,
        vix_ticker: str = "^VIX",
        vix3m_ticker: str = "^VIX3M",
        trade_ticker: str = "VIXY",
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
            contango=contango,
            backwardation=backwardation,
            weight=weight,
            vix_ticker=vix_ticker,
            vix3m_ticker=vix3m_ticker,
            trade_ticker=trade_ticker,
            inverse_ticker=inverse_ticker,
            **params,
        )

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return vix_basis_signal(
            ctx,
            vix_ticker=str(self.vix_ticker),
            vix3m_ticker=str(self.vix3m_ticker),
            trade_ticker=str(self.trade_ticker),
            inverse_ticker=str(self.inverse_ticker),
            contango=float(self.contango),
            backwardation=float(self.backwardation),
            weight=float(self.weight),
            long_only=bool(self.long_only),
        )
