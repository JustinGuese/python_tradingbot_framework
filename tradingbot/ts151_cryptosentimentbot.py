"""TS151 Crypto Sentiment Bot (§18.3)."""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_macro import crypto_sentiment_weights, fetch_crypto_fng


class TS151CryptoSentimentBot(TS151Bot):
    """Crypto Fear & Greed sentiment index strategy (§18.3).

    Fetches the alternative.me Crypto Fear & Greed index.
    The value for date D is used point-in-time from D+1 onwards.
    Momentum default longs BTC when F&G > long_above (50).
    Contrarian mode longs BTC when F&G < buy_below (25).
    """

    BOT_NAME = "ts151_cryptosentimentbot"
    DEFAULT_UNIVERSE = ("BTC-USD",)
    ALLOW_SHORT = False
    LONG_ONLY = True
    MAX_GROSS = 1.0
    NEEDS_NETWORK = True

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "long_above": 50.0,
        "buy_below": 25.0,
        "contrarian": False,
        "asset": "BTC-USD",
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "long_above": [40.0, 50.0, 60.0],
        "buy_below": [20.0, 25.0, 30.0],
        "contrarian": [False, True],
        "asset": ["BTC-USD"],
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
        self._fng_series = None

    def _get_fng(self):
        if self._fng_series is None:
            try:
                self._fng_series = fetch_crypto_fng()
            except Exception:
                self._fng_series = None
        return self._fng_series

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        fng = getattr(ctx, "fng_series", None)
        if fng is None:
            fng = self._get_fng()

        return crypto_sentiment_weights(
            as_of=ctx.as_of,
            fng_series=fng,
            asset=self.asset,
            long_above=self.long_above,
            buy_below=self.buy_below,
            contrarian=self.contrarian,
        )
