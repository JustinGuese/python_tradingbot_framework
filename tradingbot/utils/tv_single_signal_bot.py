"""Shared base for single-signal daily TradingView trend bots (paper)."""

from __future__ import annotations

from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.tv_signals import UNIVERSE_WIDE

DEFAULT_GROSS_CAP = 1.0
DEFAULT_MIN_ROWS = 60
DEFAULT_USE_DEFENSIVE = False
DEFAULT_DEFENSIVE_ASSETS: tuple[str, ...] = ("TLT", "IEF", "GLD", "SHV")


class TvSingleSignalDailyBot(Bot):
    """Daily long/flat bot driven by ONE TradingView signal across UNIVERSE_WIDE.

    Subclasses set KIND. Uses the S1b ship-rule params and min-hold for that signal.
    Equal weight (cap 0.20/ticker) among tickers whose signal is long.
    """

    KIND: ClassVar[str] = ""
    UNIVERSE: ClassVar[list[str] | None] = None  # None = UNIVERSE_WIDE
    BACKTEST_PERIOD: ClassVar[str | None] = "max"
    param_grid: ClassVar[dict] = {"gross_cap": [0.8, 1.0]}

    def __init__(
        self,
        gross_cap: float = DEFAULT_GROSS_CAP,
        min_rows: int = DEFAULT_MIN_ROWS,
        use_defensive: bool = DEFAULT_USE_DEFENSIVE,
        defensive_assets: tuple[str, ...] = DEFAULT_DEFENSIVE_ASSETS,
        **kwargs,
    ):
        super().__init__(
            type(self).__name__,
            tickers=list(type(self).UNIVERSE or UNIVERSE_WIDE),
            interval="1d",
            period="5y",
            gross_cap=gross_cap,
            min_rows=min_rows,
            **kwargs,
        )
        self.gross_cap = gross_cap
        self.min_rows = min_rows
        self.use_defensive = use_defensive
        self.defensive_assets = defensive_assets

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        frames = {
            t: self.datas[t]
            for t in self.tradeable_tickers
            if self.datas.get(t) is not None and not self.datas[t].empty
        }
        if len(frames) < len(self.tradeable_tickers):
            return {}
        from tradingbot.tvtrenddailybot import WINNING_MIN_HOLD, WINNING_SIGNAL_PARAMS, compute_weights

        return compute_weights(
            frames,
            signal_params=WINNING_SIGNAL_PARAMS,
            min_holds=WINNING_MIN_HOLD,
            min_votes=1,
            gross_cap=self.gross_cap,
            min_rows=self.min_rows,
            kinds=(self.KIND,),
            use_defensive=self.use_defensive,
            defensive_assets=self.defensive_assets,
        )
