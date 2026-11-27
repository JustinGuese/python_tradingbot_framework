"""Base class and context contract for TS151 strategy bots (PLAN-ts151.md S0)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.cash_park import park_idle_cash
from tradingbot.utils.live_state import live_holdings
from tradingbot.utils.quantsmd_common import closes_frame
from tradingbot.utils.ts151 import universes
from tradingbot.utils.ts151.common import rebalance_due

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Ctx:
    """Evaluation context passed to a bot's signal() function."""

    closes: pd.DataFrame
    datas: dict[str, pd.DataFrame]
    as_of: pd.Timestamp | Any
    universe: list[str] | tuple[str, ...]
    bot: Any


class TS151Bot(Bot):
    """Base class for all TS151 strategy bots.

    Subclasses configure class attributes (BOT_NAME, DEFAULT_UNIVERSE, etc.)
    and implement signal(ctx) -> dict[str, float] | None.
    """

    BOT_NAME: str = "TS151Bot"
    DEFAULT_UNIVERSE: str | tuple[str, ...] = ()
    BENCHMARKS: tuple[str, ...] = ()
    ALLOW_SHORT: ClassVar[bool] = False
    MAX_GROSS: ClassVar[float] = 1.0
    BACKTEST_PERIOD: ClassVar[str | None] = "max"
    LIVE_PERIOD: ClassVar[str] = "3y"
    MIN_UNIVERSE_COVERAGE: ClassVar[float] = 1.0
    param_grid: ClassVar[dict[str, list[Any]] | None] = None
    NEEDS_DB: bool = False
    NEEDS_NETWORK: bool = False

    LONG_ONLY: ClassVar[bool] = False
    PARK_CASH: ClassVar[bool] = False
    DEFAULTS: ClassVar[dict[str, Any]] = {}

    def __init__(
        self,
        *,
        interval: str = "1d",
        period: str | None = None,
        rebalance: str = "M",
        universe: str | tuple[str, ...] | list[str] | None = None,
        long_only: bool | None = None,
        park_cash: bool | None = None,
        **params: Any,
    ):
        if long_only is None:
            long_only = getattr(self, "LONG_ONLY", not getattr(self, "ALLOW_SHORT", False))
        if park_cash is None:
            park_cash = getattr(self, "PARK_CASH", False)
        if period is None:
            period = getattr(self, "LIVE_PERIOD", "3y")

        # Merge params over class DEFAULTS and set attributes
        merged = dict(getattr(self, "DEFAULTS", {}))
        merged.update(params)
        for k, v in merged.items():
            setattr(self, k, v)

        # Resolve universe
        u_raw = universe if universe is not None else getattr(self, "DEFAULT_UNIVERSE", ())
        resolved_universe = universes.resolve(u_raw)
        self.universe = u_raw
        self.universe_list = list(resolved_universe)
        self.rebalance = rebalance
        self.long_only = bool(long_only)
        self.park_cash = bool(park_cash)

        benchmarks = list(getattr(self, "BENCHMARKS", ()))
        self.BENCHMARKS = tuple(benchmarks)

        tickers = list(
            dict.fromkeys(self.universe_list + benchmarks + (["SHV"] if self.park_cash else []))
        )

        # Pre-populate instance attributes for environments where Bot.__init__ is mocked
        self.tickers = tickers
        self.benchmark_tickers = benchmarks
        self.interval = interval
        self.period = period
        self.params = {
            "rebalance": rebalance,
            "universe": u_raw,
            "long_only": self.long_only,
            "park_cash": self.park_cash,
            **merged,
        }
        self.datas = getattr(self, "datas", {})

        bot_name = getattr(self, "BOT_NAME", type(self).__name__)
        super().__init__(
            bot_name,
            tickers=tickers,
            interval=interval,
            period=period,
            benchmark_tickers=benchmarks,
            rebalance=rebalance,
            universe=u_raw,
            long_only=self.long_only,
            park_cash=self.park_cash,
            **merged,
        )

        if self.ALLOW_SHORT and not self.long_only:
            self.SHORTABLE = tuple(self.universe_list)
        else:
            self.SHORTABLE = ()

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float] | None:
        """Calculate target portfolio weights across the universe."""
        closes = closes_frame(self.datas, self.tickers)
        if closes.empty:
            return {}

        holdings = live_holdings(self, rows)
        is_flat = holdings is not None and not holdings

        # Gate: only rebalance when flat (initial build) or when rebalance is due
        if not is_flat and not rebalance_due(closes.index, self.rebalance):
            return None

        ctx = Ctx(
            closes=closes,
            datas=self.datas,
            as_of=closes.index[-1],
            universe=self.universe_list,
            bot=self,
        )

        w = self.signal(ctx)
        if w is None:
            return None
        if not w:
            return {}

        # Long-only handling: drop negative weights and rescale positive weights
        if self.long_only:
            pos = {str(k): float(v) for k, v in w.items() if v > 0}
            if not pos:
                return {}
            orig_long_sum = sum(pos.values())
            target_sum = min(1.0, orig_long_sum)
            w = {k: float(v * (target_sum / orig_long_sum)) for k, v in pos.items()}

        # Idle cash parking into cash proxy ETF
        if self.park_cash and w:
            max_g = float(getattr(self, "MAX_GROSS", 1.0)) - 0.1
            w = park_idle_cash(w, proxy="SHV", max_gross=max_g)

        return w

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        """Strategy signal logic implemented by subclasses.

        Returns dict[str, float] of target weights, {} for flat/cash, or None for no rebalance.
        """
        raise NotImplementedError("Subclasses must implement signal(ctx)")
