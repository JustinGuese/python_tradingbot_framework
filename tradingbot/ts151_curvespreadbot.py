"""TS151 Yield Curve Slope / Spread Bot (§5.13).

Source: Kakushadze & Serur (2018) §5.13 "Yield curve slope".
Slope = 30Y Treasury yield − 2Y Treasury yield (from FRED DGS30 and DGS2).
When slope z-score < -entry=1.0: steepener (long SHY, short TLT, DV01-neutral).
When slope z-score > 1.0: flattener (short SHY, long TLT, DV01-neutral).
Exit when abs(z) < exit=0.25.
Proxy: SHY and TLT.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_rates import curve_spread_signal, load_fred_curve_slope

logger = logging.getLogger(__name__)


class TS151CurveSpreadBot(TS151Bot):
    """Yield Curve Slope Strategy (§5.13).

    Kakushadze & Serur (2018) §5.13 "Yield curve slope".
    Universe: SHY, TLT.
    Rebalance: Daily ("D").
    Trades steepener or flattener based on 30Y - 2Y slope z-score.
    """

    BOT_NAME: str = "ts151_CurveSpreadBot"
    DEFAULT_UNIVERSE: tuple[str, ...] = ("SHY", "TLT")
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
        "short_asset": "SHY",
        "long_asset": "TLT",
        "gross": 1.0,
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "window": [126, 252],
        "entry": [0.75, 1.0, 1.5],
        "exit": [0.25, 0.5],
        "short_asset": ["SHY"],
        "long_asset": ["TLT"],
        "gross": [1.0],
    }

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rebalance", "D")
        super().__init__(**kwargs)

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        try:
            slope = load_fred_curve_slope(ctx.closes.index)
        except Exception as exc:
            logger.warning("Could not load FRED slope: %s", exc)
            return None

        return curve_spread_signal(
            slope,
            window=int(self.window),
            entry=float(self.entry),
            exit_level=float(self.exit),
            short_asset=str(self.short_asset),
            long_asset=str(self.long_asset),
            gross=float(self.gross),
        )
