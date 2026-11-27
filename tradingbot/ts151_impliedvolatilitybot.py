"""Kakushadze & Serur (2018) §3.5: Implied Volatility.

Score = Δ(window=21) call ATM IV - Δ put ATM IV from vol_surface (or -Δ ATM IV).
Long top `frac=0.2`, short bottom `frac=0.2`.
Forward-only (history in vol_surface starts 2026-09).
Defaults: window=21, frac=0.2, gross=2.0.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pandas as pd

from tradingbot.utils.db import VolSurfaceSnapshot, get_db_session
from tradingbot.utils.runner import run_bot
from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_stocks import (
    check_vol_surface_staleness,
    compute_implied_volatility_weights,
)
from tradingbot.utils.universes import OPTION_CAPTURE_UNIVERSE


class TS151ImpliedVolatilityBot(TS151Bot):
    """Implied Volatility change strategy (§3.5).

    Forward-only bot operating on single names captured in vol_surface.
    Ranks by change in implied volatility skew / ATM implied volatility over window bars.
    """

    BOT_NAME = "ts151_ImpliedVolatilityBot"
    DEFAULT_UNIVERSE = tuple(OPTION_CAPTURE_UNIVERSE[2:])
    BENCHMARKS = ()
    ALLOW_SHORT = True
    LONG_ONLY = False
    MAX_GROSS = 2.0
    BACKTEST_PERIOD = "max"
    LIVE_PERIOD = "3y"
    MIN_UNIVERSE_COVERAGE = 0.8
    NEEDS_DB = True

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "window": 21,
        "frac": 0.2,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "window": [10, 21, 42],
        "frac": [0.1, 0.2, 0.3],
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
        window: int = 21,
        frac: float = 0.2,
        **params: Any,
    ):
        super().__init__(
            interval=interval,
            period=period,
            rebalance=rebalance,
            universe=universe,
            long_only=long_only,
            park_cash=park_cash,
            window=window,
            frac=frac,
            **params,
        )
        self._vol_surface_cache: pd.DataFrame | None = None

    def _get_vol_surface(self) -> pd.DataFrame:
        if self._vol_surface_cache is None:
            with get_db_session() as session:
                rows = (
                    session.query(VolSurfaceSnapshot)
                    .filter(VolSurfaceSnapshot.underlying.in_(self.universe_list))
                    .all()
                )
                if not rows:
                    self._vol_surface_cache = pd.DataFrame(
                        columns=["underlying", "snapshot_date", "atm_iv_30", "iv_25c_30", "iv_25p_30"]
                    )
                else:
                    self._vol_surface_cache = pd.DataFrame([
                        {
                            "underlying": r.underlying,
                            "snapshot_date": r.snapshot_date,
                            "atm_iv_30": r.atm_iv_30,
                            "iv_25c_30": r.iv_25c_30,
                            "iv_25p_30": r.iv_25p_30,
                        }
                        for r in rows
                    ])
        return self._vol_surface_cache

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        vol_frame = self._get_vol_surface()
        if not check_vol_surface_staleness(vol_frame, ctx.as_of):
            return None
        return compute_implied_volatility_weights(
            vol_frame=vol_frame,
            universe=ctx.universe,
            as_of=ctx.as_of,
            window=int(self.window),
            frac=float(self.frac),
            long_only=self.long_only,
            gross=float(self.MAX_GROSS),
        )


if __name__ == "__main__":
    run_bot(TS151ImpliedVolatilityBot)
