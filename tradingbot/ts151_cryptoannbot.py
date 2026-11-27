"""TS151 Crypto ANN Bot (§18.2)."""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_macro import crypto_ann_weights


class TS151CryptoANNBot(TS151Bot):
    """Neural network (MLP) trend forecasting on crypto (§18.2).

    Per coin, trains a multi-layer perceptron on lagged returns (1, 3, 7, 14, 30)
    over a trailing rolling window without forward lookahead leakage.
    Longs if predicted forward probability exceeds threshold.
    """

    BOT_NAME = "ts151_cryptoannbot"
    DEFAULT_UNIVERSE = "crypto"
    ALLOW_SHORT = False
    LONG_ONLY = True
    MAX_GROSS = 1.0
    LIVE_PERIOD = "5y"

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "lags": (1, 3, 7, 14, 30),
        "horizon": 1,
        "train": 730,
        "threshold": 0.55,
        "hidden_layer_sizes": (16, 8),
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "lags": [(1, 3, 7, 14, 30), (1, 5, 10, 20)],
        "horizon": [1, 3],
        "train": [365, 730],
        "threshold": [0.50, 0.55, 0.60],
        "hidden_layer_sizes": [(16, 8), (32, 16)],
    }

    def __init__(
        self,
        *,
        interval: str = "1d",
        period: str | None = None,
        rebalance: str = "D",
        universe: str | tuple[str, ...] | list[str] | None = None,
        long_only: bool = True,
        park_cash: bool = False,
        **params: Any,
    ):
        super().__init__(
            interval=interval,
            period=period,
            rebalance=rebalance,
            universe=universe,
            long_only=long_only,
            park_cash=park_cash,
            **params,
        )

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return crypto_ann_weights(
            closes=ctx.closes,
            as_of=ctx.as_of,
            lags=self.lags,
            horizon=self.horizon,
            train=self.train,
            threshold=self.threshold,
            hidden_layer_sizes=self.hidden_layer_sizes,
            gross=float(self.MAX_GROSS),
        )
