"""NeutralStockBot — Cross-sectional market-neutral stock book on the S&P 500 (F2).

Monthly rebalancing of an equal-weighted top-k long / bottom-k short book
derived from composite residual momentum (12-1 residuals from 36-month OLS
regression on the market) and 1-month short-term reversal, strictly standardized
within GICS sectors.

Key design contracts:
- Monthly rebalance on first bar of month (or build when flat).
- Universe: current S&P 500 (SP500) and SP500_SECTOR mapping from universes.py.
- Size: k = 25 names per side (50 total names: whole-share shorts at paper book scale).
- Leverage / Reg T: target long = 0.9, target short = -0.9, gross = 1.8 <= 2.0.
- Equal counts per sector side strictly maintained.
- Idle cash capacity parked in SHV via park_idle_cash (S0d).
- Short framework: ALLOW_SHORT=True, SHORTABLE=SP500.

Backtest and evaluation: docs/backtests/neutral-book-2026-11.md.
Paper only.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.cash_park import park_idle_cash
from tradingbot.utils.cs_neutral_book import build_cs_neutral_book
from tradingbot.utils.live_state import live_holdings
from tradingbot.utils.quantsmd_common import closes_frame, period_rolled
from tradingbot.utils.runner import run_bot
from tradingbot.utils.universes import SP500, SP500_SECTOR

logger = logging.getLogger(__name__)


class NeutralStockBot(Bot):
    """Monthly cross-sectional market-neutral stock book on the S&P 500."""

    BACKTEST_PERIOD: ClassVar[str | None] = "max"
    MIN_UNIVERSE_COVERAGE: ClassVar[float] = 0.80
    ALLOW_SHORT: ClassVar[bool] = True
    MAX_GROSS: ClassVar[float] = 2.0
    SHORTABLE: ClassVar[tuple[str, ...]] = SP500
    CASH_PROXY: ClassVar[str] = "SHV"

    def __init__(
        self,
        k: int = 25,
        weight_mom: float = 0.5,
        weight_rev: float = 0.5,
        target_long: float = 0.9,
        target_short: float = 0.9,
        park_cash: bool = True,
        beta_scale: bool = False,
        **kwargs: Any,
    ) -> None:
        tickers = list(dict.fromkeys((*SP500, "SPY", "QQQ", self.CASH_PROXY)))
        super().__init__(
            "NeutralStockBot",
            tickers=tickers,
            interval="1d",
            period="3y",
            k=k,
            weight_mom=weight_mom,
            weight_rev=weight_rev,
            target_long=target_long,
            target_short=target_short,
            park_cash=park_cash,
            beta_scale=beta_scale,
            **kwargs,
        )
        self.k = k
        self.weight_mom = weight_mom
        self.weight_rev = weight_rev
        self.target_long = target_long
        self.target_short = target_short
        self.park_cash = park_cash
        self.beta_scale = beta_scale

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float] | None:
        """Return target weights for the neutral stock book.

        Rebalances on the first bar of each calendar month or whenever the portfolio
        is flat. Returns None mid-month when holdings are already present.
        """
        closes = closes_frame(self.datas, self.tickers)
        if closes.empty:
            return {}

        holdings = live_holdings(self, rows)
        is_flat = holdings is not None and not holdings
        month_rolled = period_rolled(closes.index, "M")

        # Monthly rebalance gate: only rebalance on month roll or when flat
        if not month_rolled and not is_flat:
            return None

        weights = build_cs_neutral_book(
            prices=closes,
            sector_map=SP500_SECTOR,
            market_prices="SPY",
            benchmark_prices="QQQ",
            weight_mom=self.weight_mom,
            weight_rev=self.weight_rev,
            k=self.k,
            target_long=self.target_long,
            target_short=self.target_short,
            beta_scale=self.beta_scale,
        )

        if not weights:
            return {}

        if self.park_cash:
            weights = park_idle_cash(
                weights, proxy=self.CASH_PROXY, max_gross=self.MAX_GROSS - 0.1
            )  # keep Reg T headroom

        logger.info(
            "%s: rebalanced %d positions (gross %.2f) -> %s",
            self.bot_name,
            len(weights),
            sum(abs(w) for w in weights.values()),
            weights,
        )
        return weights


if __name__ == "__main__":
    run_bot(NeutralStockBot)
