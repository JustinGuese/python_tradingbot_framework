"""Kakushadze & Serur (2018) §15.3: Distressed Securities.

Simplified Campbell-Hilscher-Szilagyi (2008) distress score:
Score = -NI/TA + TL/TA - CASH/TA + σ(window=63) - excess_return(window=21) - log(mcap).
Long lowest distress (financially sound), short highest distress (distressed), frac=0.1.
Defaults: vol_window=63, ret_window=21, frac=0.1, gross=2.0.
Proxy: Balance sheet NetIncomeLoss, Assets, Liabilities, Cash from SEC XBRL company facts.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pandas as pd

from tradingbot.utils.runner import run_bot
from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.data_api import load_fundamentals
from tradingbot.utils.ts151.signals_stocks import (
    check_sec_fundamentals_staleness,
    compute_distress_weights,
)


class TS151DistressBot(TS151Bot):
    """Distressed Securities strategy (§15.3).

    Quantitative insolvency risk model combining balance sheet leverage, profitability,
    liquidity, equity volatility, short-term underperformance, and size.
    """

    BOT_NAME = "ts151_DistressBot"
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
        "vol_window": 63,
        "ret_window": 21,
        "frac": 0.1,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "vol_window": [42, 63, 126],
        "ret_window": [10, 21, 42],
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
        vol_window: int = 63,
        ret_window: int = 21,
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
            vol_window=vol_window,
            ret_window=ret_window,
            frac=frac,
            **params,
        )
        self._fundamentals_cache: pd.DataFrame | None = None

    def _get_fundamentals(self) -> pd.DataFrame:
        if self._fundamentals_cache is None:
            self._fundamentals_cache = load_fundamentals(
                tickers=self.universe_list,
                tags=[
                    "NetIncomeLoss",
                    "Assets",
                    "Liabilities",
                    "CashAndCashEquivalentsAtCarryingValue",
                    "EntityCommonStockSharesOutstanding",
                ],
            )
        return self._fundamentals_cache

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        fundamentals = self._get_fundamentals()
        if not check_sec_fundamentals_staleness(fundamentals, ctx.universe, ctx.as_of):
            return None
        return compute_distress_weights(
            closes=ctx.closes,
            fundamentals=fundamentals,
            as_of=ctx.as_of,
            vol_window=int(self.vol_window),
            ret_window=int(self.ret_window),
            frac=float(self.frac),
            long_only=self.long_only,
            gross=float(self.MAX_GROSS),
        )


if __name__ == "__main__":
    run_bot(TS151DistressBot)
