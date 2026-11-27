"""TS151 Inflation Breakeven Arbitrage Bot (§14.1).

Source: Kakushadze & Serur (2018) §14.1 "Inflation breakeven arbitrage".
Trades duration-matched TIP vs IEF based on the 10-Year Breakeven Inflation Rate (FRED T10YIE).
mode="breakeven": Long TIP / short IEF when T10YIE is below window mean - entry * std; reverse above.
mode="tips_treasury": Always long TIP / short IEF when T10YIE < fair (default 2.0%).
Proxy: TIP and IEF.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_rates import breakeven_signal, load_fred_breakeven_series

logger = logging.getLogger(__name__)


class TS151BreakevenBot(TS151Bot):
    """Inflation Breakeven Strategy (§14.1).

    Kakushadze & Serur (2018) §14.1 "Inflation breakeven arbitrage".
    Universe: TIP, IEF.
    Rebalance: Daily ("D").
    Exploits mispricings between nominal Treasuries and TIPS.
    """

    BOT_NAME: str = "ts151_BreakevenBot"
    DEFAULT_UNIVERSE: tuple[str, ...] = ("TIP", "IEF")
    ALLOW_SHORT: ClassVar[bool] = True
    LONG_ONLY: ClassVar[bool] = False
    MAX_GROSS: ClassVar[float] = 2.0
    LIVE_PERIOD: ClassVar[str] = "3y"
    BACKTEST_PERIOD: ClassVar[str | None] = "max"
    NEEDS_DB: bool = False
    NEEDS_NETWORK: bool = True

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "mode": "breakeven",
        "window": 252,
        "entry": 1.0,
        "exit": 0.25,
        "fair": 2.0,
        "duration_tip": 7.0,
        "duration_ief": 7.5,
        "gross": 1.0,
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "mode": ["breakeven", "tips_treasury"],
        "window": [126, 252],
        "entry": [0.75, 1.0, 1.5],
        "exit": [0.25, 0.5],
        "fair": [1.5, 2.0, 2.5],
        "duration_tip": [7.0],
        "duration_ief": [7.5],
        "gross": [1.0],
    }

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rebalance", "D")
        super().__init__(**kwargs)

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        try:
            t10yie = load_fred_breakeven_series(ctx.closes.index)
        except Exception as exc:
            logger.warning("Could not load FRED T10YIE: %s", exc)
            return None

        return breakeven_signal(
            t10yie,
            mode=str(self.mode),
            window=int(self.window),
            entry=float(self.entry),
            exit_level=float(self.exit),
            fair=float(self.fair),
            duration_tip=float(self.duration_tip),
            duration_ief=float(self.duration_ief),
            gross=float(self.gross),
        )
