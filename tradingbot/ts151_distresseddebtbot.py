"""TS151 Distressed Debt Strategy Bot (§15.1).

Source: Kakushadze & Serur (2018) §15.1 "Distressed debt".
Long high-yield bonds (HYG) when the HYG/IEF ratio drawdown from its window=252 high is < -entry=0.08.
Hold until it recovers to -exit=0.02.
Otherwise parked in cash proxy (SHV).
Proxy: HYG, IEF, SHV.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_rates import distressed_debt_signal

logger = logging.getLogger(__name__)


class TS151DistressedDebtBot(TS151Bot):
    """Distressed Debt Strategy (§15.1).

    Kakushadze & Serur (2018) §15.1 "Distressed debt".
    Universe: HYG, IEF, SHV.
    Rebalance: Daily ("D").
    Buys distressed debt following severe relative drawdowns vs Treasuries.
    """

    BOT_NAME: str = "ts151_DistressedDebtBot"
    DEFAULT_UNIVERSE: tuple[str, ...] = ("HYG", "IEF", "SHV")
    ALLOW_SHORT: ClassVar[bool] = False
    LONG_ONLY: ClassVar[bool] = True
    MAX_GROSS: ClassVar[float] = 1.0
    LIVE_PERIOD: ClassVar[str] = "3y"
    BACKTEST_PERIOD: ClassVar[str | None] = "max"
    NEEDS_DB: bool = False
    NEEDS_NETWORK: bool = False

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "entry": 0.08,
        "exit": 0.02,
        "window": 252,
        "asset": "HYG",
        "benchmark": "IEF",
        "cash_asset": "SHV",
        "gross": 1.0,
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "entry": [0.05, 0.08, 0.12],
        "exit": [0.02, 0.04],
        "window": [126, 252],
        "asset": ["HYG"],
        "benchmark": ["IEF"],
        "cash_asset": ["SHV"],
        "gross": [1.0],
    }

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rebalance", "D")
        super().__init__(**kwargs)

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return distressed_debt_signal(
            ctx.closes,
            entry=float(self.entry),
            exit_level=float(self.exit),
            window=int(self.window),
            asset=str(self.asset),
            benchmark=str(self.benchmark),
            cash_asset=str(self.cash_asset),
            gross=float(self.gross),
        )
