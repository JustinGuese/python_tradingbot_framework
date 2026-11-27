"""Kakushadze & Serur (2018) §3.6: Multifactor.

Equal average (or factor_weights) of within-sector z-scores of momentum (§3.1),
value (§3.3) and -vol (§3.4).
Long top `frac=0.1`, short bottom `frac=0.1`.
Defaults: mom_window=252, mom_skip=21, vol_window=252, frac=0.1, factor_weights=(1.0, 1.0, 1.0), gross=2.0.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pandas as pd

from tradingbot.utils.runner import run_bot
from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.data_api import load_fundamentals
from tradingbot.utils.ts151.signals_stocks import (
    check_sec_fundamentals_staleness,
    compute_multifactor_weights,
)


class TS151MultifactorBot(TS151Bot):
    """Multifactor strategy (§3.6).

    Combines momentum, book-to-price value, and low-volatility within sectors,
    going long top decile and short bottom decile.
    """

    BOT_NAME = "ts151_MultifactorBot"
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
        "mom_window": 252,
        "mom_skip": 21,
        "vol_window": 252,
        "frac": 0.1,
        "factor_weights": (1.0, 1.0, 1.0),
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "mom_window": [126, 252],
        "mom_skip": [0, 21],
        "vol_window": [126, 252],
        "frac": [0.05, 0.1, 0.2],
        "factor_weights": [(1.0, 1.0, 1.0), (2.0, 1.0, 1.0)],
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
        mom_window: int = 252,
        mom_skip: int = 21,
        vol_window: int = 252,
        frac: float = 0.1,
        factor_weights: tuple[float, float, float] = (1.0, 1.0, 1.0),
        **params: Any,
    ):
        super().__init__(
            interval=interval,
            period=period,
            rebalance=rebalance,
            universe=universe,
            long_only=long_only,
            park_cash=park_cash,
            mom_window=mom_window,
            mom_skip=mom_skip,
            vol_window=vol_window,
            frac=frac,
            factor_weights=factor_weights,
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
        return compute_multifactor_weights(
            closes=ctx.closes,
            fundamentals=fundamentals,
            as_of=ctx.as_of,
            mom_window=int(self.mom_window),
            mom_skip=int(self.mom_skip),
            vol_window=int(self.vol_window),
            frac=float(self.frac),
            factor_weights=self.factor_weights,
            long_only=self.long_only,
            gross=float(self.MAX_GROSS),
        )


if __name__ == "__main__":
    run_bot(TS151MultifactorBot)
