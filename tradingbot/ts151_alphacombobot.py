"""Kakushadze & Serur (2018) §3.20: Alpha Combination.

Alphas = momentum, reversal(5), low-vol, value.
Each alpha's long-short return series over train=252 bars.
Combination weights ∝ mean/var of those returns (clipped ≥ 0).
Combined score → top/bottom frac=0.1.
Defaults: train=252, frac=0.1, gross=2.0.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pandas as pd

from tradingbot.utils.runner import run_bot
from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.data_api import load_fundamentals
from tradingbot.utils.ts151.signals_stocks import (
    check_sec_fundamentals_staleness,
    compute_alphacombo_weights,
)


class TS151AlphaComboBot(TS151Bot):
    """Alpha Combination strategy (§3.20).

    Dynamic ensemble combining price momentum, short-term reversal, low volatility,
    and value according to trailing risk-adjusted performance (mean/variance).
    """

    BOT_NAME = "ts151_AlphaComboBot"
    DEFAULT_UNIVERSE = "sp500"
    BENCHMARKS = ()
    ALLOW_SHORT = True
    LONG_ONLY = False
    MAX_GROSS = 2.0
    BACKTEST_PERIOD = "max"
    LIVE_PERIOD = "3y"
    MIN_UNIVERSE_COVERAGE = 0.8
    NEEDS_DB = True

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "train": 252,
        "frac": 0.1,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "train": [126, 252],
        "frac": [0.05, 0.1, 0.2],
    }

    def __init__(
        self,
        *,
        interval: str = "1d",
        period: str | None = None,
        rebalance: str = "M",
        universe: str | tuple[str, ...] | list[str] | None = None,
        long_only: bool | None = None,
        park_cash: bool | None = None,
        train: int = 252,
        frac: float = 0.1,
        **params: Any,
    ):
        super().__init__(
            interval=interval,
            period=period,
            rebalance=rebalance,
            universe=universe,
            long_only=long_only,
            park_cash=park_cash,
            train=train,
            frac=frac,
            **params,
        )
        self._fundamentals_cache: pd.DataFrame | None = None

    def _get_fundamentals(self) -> pd.DataFrame:
        if self._fundamentals_cache is None:
            self._fundamentals_cache = load_fundamentals(
                tickers=self.universe_list,
                tags=["StockholdersEquity", "EntityCommonStockSharesOutstanding"],
            )
        return self._fundamentals_cache

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        fundamentals = self._get_fundamentals()
        if not check_sec_fundamentals_staleness(fundamentals, ctx.universe, ctx.as_of):
            return None
        return compute_alphacombo_weights(
            closes=ctx.closes,
            fundamentals=fundamentals,
            as_of=ctx.as_of,
            train=int(self.train),
            frac=float(self.frac),
            gross=float(self.MAX_GROSS),
        )


if __name__ == "__main__":
    run_bot(TS151AlphaComboBot)
