"""TS151 MBS Arbitrage Bot (§11.7).

Source: Kakushadze & Serur (2018) §11.7 "Mortgage-backed securities arbitrage".
Long MBB / short IEF (duration-matched) when log(MBB/IEF) z-score < -entry=1.0 (wide spread).
Exit at exit=0.0.
Proxy: MBB (mortgage-backed securities ETF) and IEF (Treasury 7-10y ETF).
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_rates import mbs_signal

logger = logging.getLogger(__name__)


class TS151MbsBot(TS151Bot):
    """MBS Arbitrage Strategy (§11.7).

    Kakushadze & Serur (2018) §11.7 "Mortgage-backed securities arbitrage".
    Universe: MBB, IEF.
    Rebalance: Daily ("D").
    Exploits mortgage spread widening vs Treasuries, duration-matched.
    """

    BOT_NAME: str = "ts151_MbsBot"
    DEFAULT_UNIVERSE: tuple[str, ...] = ("MBB", "IEF")
    ALLOW_SHORT: ClassVar[bool] = True
    LONG_ONLY: ClassVar[bool] = False
    MAX_GROSS: ClassVar[float] = 2.0
    LIVE_PERIOD: ClassVar[str] = "3y"
    BACKTEST_PERIOD: ClassVar[str | None] = "max"
    NEEDS_DB: bool = False
    NEEDS_NETWORK: bool = False

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "window": 252,
        "entry": 1.0,
        "exit": 0.0,
        "duration_mbb": 6.0,
        "duration_ief": 7.5,
        "gross": 1.0,
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "window": [126, 252],
        "entry": [0.75, 1.0, 1.5],
        "exit": [0.0, 0.25],
        "duration_mbb": [6.0],
        "duration_ief": [7.5],
        "gross": [1.0],
    }

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rebalance", "D")
        super().__init__(**kwargs)

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return mbs_signal(
            ctx.closes,
            window=int(self.window),
            entry=float(self.entry),
            exit_level=float(self.exit),
            duration_mbb=float(self.duration_mbb),
            duration_ief=float(self.duration_ief),
            gross=float(self.gross),
        )
