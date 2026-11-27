"""TS151 Dollar-Duration Butterfly Bot (§5.6).

Source: Kakushadze & Serur (2018) §5.6 "Dollar-duration-neutral butterfly".
Body IEF vs wings SHY+TLT, dollar-duration neutral.
Signal = body yield − average wing yield (from FRED), z-score over window=252.
When z > entry=1.0: long body / short wings.
When z < -1.0: short body / long wings.
Exit when abs(z) < exit=0.25.
Proxy: IEF (body), SHY and TLT (wings).
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_rates import dd_butterfly_signal, load_fred_spread_series

logger = logging.getLogger(__name__)


class TS151DdButterflyBot(TS151Bot):
    """Dollar-Duration Butterfly Strategy (§5.6).

    Kakushadze & Serur (2018) §5.6 "Dollar-duration-neutral butterfly".
    Universe: SHY, IEF, TLT.
    Rebalance: Daily ("D").
    Long/short butterfly trading curve curvature via FRED yields.
    """

    BOT_NAME: str = "ts151_DdButterflyBot"
    DEFAULT_UNIVERSE: tuple[str, ...] = ("SHY", "IEF", "TLT")
    ALLOW_SHORT: ClassVar[bool] = True
    LONG_ONLY: ClassVar[bool] = False
    MAX_GROSS: ClassVar[float] = 2.0
    LIVE_PERIOD: ClassVar[str] = "3y"
    BACKTEST_PERIOD: ClassVar[str | None] = "max"
    NEEDS_DB: bool = False
    NEEDS_NETWORK: bool = True

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "window": 252,
        "entry": 1.0,
        "exit": 0.25,
        "body": "IEF",
        "wings": ("SHY", "TLT"),
        "gross": 1.0,
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "window": [126, 252],
        "entry": [0.75, 1.0, 1.5],
        "exit": [0.25, 0.5],
        "body": ["IEF"],
        "wings": [("SHY", "TLT")],
        "gross": [1.0],
    }

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rebalance", "D")
        super().__init__(**kwargs)

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        try:
            spread, _, _, _ = load_fred_spread_series(
                ctx.closes.index,
                body=str(self.body),
                wings=tuple(self.wings),
            )
        except Exception as exc:
            logger.warning("Could not load FRED yields: %s", exc)
            return None

        return dd_butterfly_signal(
            spread,
            window=int(self.window),
            entry=float(self.entry),
            exit_level=float(self.exit),
            gross=float(self.gross),
            body=str(self.body),
            wings=tuple(self.wings),
        )
