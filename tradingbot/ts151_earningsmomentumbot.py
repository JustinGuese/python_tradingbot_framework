"""Kakushadze & Serur (2018) §3.2: Earnings Momentum (SUE).

Score = SUE (Standardized Unexpected Earnings) = (EPS_q - EPS_{q-4}) / std(over last n_q=8 quarters).
Long top `frac=0.1`, short bottom `frac=0.1`.
Defaults: n_q=8, frac=0.1, gross=2.0.
Proxy: Quarterly diluted EPS from SEC XBRL company facts (EarningsPerShareDiluted).
"""

from __future__ import annotations

from typing import Any, ClassVar

import pandas as pd

from tradingbot.utils.runner import run_bot
from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.data_api import load_fundamentals
from tradingbot.utils.ts151.signals_stocks import (
    check_sec_fundamentals_staleness,
    compute_earnings_momentum_weights,
)


class TS151EarningsMomentumBot(TS151Bot):
    """Earnings Momentum strategy (§3.2).

    Ranks stocks by Standardized Unexpected Earnings (SUE), measured as year-over-year
    quarterly EPS change divided by trailing standard deviation.
    """

    BOT_NAME = "ts151_EarningsMomentumBot"
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
        "n_q": 8,
        "frac": 0.1,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "n_q": [6, 8, 12],
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
        n_q: int = 8,
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
            n_q=n_q,
            frac=frac,
            **params,
        )
        self._fundamentals_cache: pd.DataFrame | None = None

    def _get_fundamentals(self) -> pd.DataFrame:
        if self._fundamentals_cache is None:
            self._fundamentals_cache = load_fundamentals(
                tickers=self.universe_list,
                tags=["EarningsPerShareDiluted"],
            )
        return self._fundamentals_cache

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        fundamentals = self._get_fundamentals()
        if not check_sec_fundamentals_staleness(fundamentals, ctx.universe, ctx.as_of):
            return None
        return compute_earnings_momentum_weights(
            fundamentals=fundamentals,
            universe=ctx.universe,
            as_of=ctx.as_of,
            n_q=int(self.n_q),
            frac=float(self.frac),
            long_only=self.long_only,
            gross=float(self.MAX_GROSS),
        )


if __name__ == "__main__":
    run_bot(TS151EarningsMomentumBot)
