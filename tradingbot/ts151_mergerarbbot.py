"""Kakushadze & Serur (2018) §3.16: Merger Arbitrage.

Dynamic universe: target tickers from merger announcements in ts151_merger_events within last `hold=120` days plus SPY.
Long each target from first bar after filed_at for `hold` bars. Equal weight, capped at `max_weight=0.1`.
Defaults: hold=120, max_weight=0.1.
Proxy: Announced cash/stock deals from EDGAR merger filings (SC TO-T, SC 14D9, DEFM14A, PREM14A).
"""

from __future__ import annotations

from typing import Any, ClassVar

import pandas as pd

from tradingbot.utils.runner import run_bot
from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.data_api import load_merger_events
from tradingbot.utils.ts151.signals_stocks import (
    check_merger_heartbeat,
    compute_merger_arb_weights,
)


class TS151MergerArbBot(TS151Bot):
    """Merger Arbitrage strategy (§3.16).

    Long-only strategy capturing deal-spread accretion on targets of merger filings.
    Dynamic universe built from recent SEC M&A announcements.
    """

    BOT_NAME = "ts151_MergerArbBot"
    DEFAULT_UNIVERSE = ("SPY",)
    BENCHMARKS = ()
    ALLOW_SHORT = False
    LONG_ONLY = True
    MAX_GROSS = 1.0
    BACKTEST_PERIOD = "max"
    LIVE_PERIOD = "3y"
    MIN_UNIVERSE_COVERAGE = 0.5
    NEEDS_DB = True

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "hold": 120,
        "max_weight": 0.1,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "hold": [60, 120, 180],
        "max_weight": [0.05, 0.1, 0.2],
    }

    def __init__(
        self,
        *,
        interval: str = "1d",
        period: str | None = None,
        rebalance: str = "D",
        universe: str | tuple[str, ...] | list[str] | None = None,
        long_only: bool | None = None,
        park_cash: bool | None = None,
        hold: int = 120,
        max_weight: float = 0.1,
        **params: Any,
    ):
        self._merger_events_cache: pd.DataFrame | None = None

        # Dynamically build universe from DB if not provided
        if universe is None:
            try:
                events = load_merger_events()
                self._merger_events_cache = events
                non_hb = events[events["accession"] != "_HEARTBEAT"]
                raw_tickers = [t for t in non_hb["ticker"].dropna().unique() if t]
                dyn_universe = tuple(dict.fromkeys(raw_tickers + ["SPY"]))
            except Exception:
                dyn_universe = ("SPY",)
        else:
            dyn_universe = universe

        super().__init__(
            interval=interval,
            period=period,
            rebalance=rebalance,
            universe=dyn_universe,
            long_only=long_only,
            park_cash=park_cash,
            hold=hold,
            max_weight=max_weight,
            **params,
        )

    def _get_events(self) -> pd.DataFrame:
        if self._merger_events_cache is None:
            self._merger_events_cache = load_merger_events()
        return self._merger_events_cache

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        events = self._get_events()
        check_merger_heartbeat(events, ctx.as_of)
        return compute_merger_arb_weights(
            merger_events=events,
            as_of=ctx.as_of,
            hold_bars=int(self.hold),
            max_weight=float(self.max_weight),
        )


if __name__ == "__main__":
    run_bot(TS151MergerArbBot)
