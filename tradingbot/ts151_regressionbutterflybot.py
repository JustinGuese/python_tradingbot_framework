"""TS151 Regression-Weighted Butterfly Bot (§5.8).

Source: Kakushadze & Serur (2018) §5.8 "Regression-weighted butterfly".
Body IEF vs wings SHY+TLT with wing weights determined by regressing Δbody yield on Δwing yields.
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
from tradingbot.utils.ts151.signals_rates import load_fred_spread_series, regression_butterfly_signal

logger = logging.getLogger(__name__)


class TS151RegressionButterflyBot(TS151Bot):
    """Regression-Weighted Butterfly Strategy (§5.8).

    Kakushadze & Serur (2018) §5.8 "Regression-weighted butterfly".
    Universe: SHY, IEF, TLT.
    Rebalance: Daily ("D").
    Wing weights estimated from rolling yield-change regression betas.
    """

    BOT_NAME: str = "ts151_RegressionButterflyBot"
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
            spread, body_y, w0_y, w1_y = load_fred_spread_series(
                ctx.closes.index,
                body=str(self.body),
                wings=tuple(self.wings),
            )
        except Exception as exc:
            logger.warning("Could not load FRED yields: %s", exc)
            return None

        return regression_butterfly_signal(
            spread_series=spread,
            body_yield=body_y,
            w0_yield=w0_y,
            w1_yield=w1_y,
            window=int(self.window),
            entry=float(self.entry),
            exit_level=float(self.exit),
            gross=float(self.gross),
            body=str(self.body),
            wings=tuple(self.wings),
        )
