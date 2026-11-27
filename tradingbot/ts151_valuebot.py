"""Kakushadze & Serur (2018) §3.3: Value (Book-to-Price).

Score = B/P = StockholdersEquity / (close * shares_outstanding).
Long top `frac=0.1`, short bottom `frac=0.1`.
Negative book value is excluded.
Defaults: frac=0.1, gross=2.0.
Proxy: Balance-sheet StockholdersEquity and shares outstanding from SEC XBRL company facts.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pandas as pd

from tradingbot.utils.runner import run_bot
from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.data_api import load_fundamentals
from tradingbot.utils.ts151.signals_stocks import (
    check_sec_fundamentals_staleness,
    compute_value_weights,
)


class TS151ValueBot(TS151Bot):
    """Value strategy (§3.3).

    Ranks stocks by Book-to-Price (B/P) ratio, holding long high B/P (value)
    and short low B/P (growth/glamour). Negative book value is excluded.
    """

    BOT_NAME = "ts151_ValueBot"
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
        "frac": 0.1,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
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
        check_sec_fundamentals_staleness(fundamentals, ctx.universe, ctx.as_of)
        return compute_value_weights(
            fundamentals=fundamentals,
            closes_row=ctx.closes.iloc[-1],
            as_of=ctx.as_of,
            frac=float(self.frac),
            long_only=self.long_only,
            gross=float(self.MAX_GROSS),
        )


if __name__ == "__main__":
    run_bot(TS151ValueBot)
