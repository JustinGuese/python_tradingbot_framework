"""Kakushadze & Serur (2018) §3.8: Pairs Trading.

Picks top `n_pairs=10` by return correlation within each sector over `formation=252` bars.
Spread = log(A) - beta * log(B) (OLS).
Enter when abs(z) > entry_z=2.0 (short rich / long cheap), exit when abs(z) < exit_z=0.5.
Defaults: formation=252, n_pairs=10, entry_z=2.0, exit_z=0.5, gross=2.0.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.runner import run_bot
from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.signals_stocks import compute_pairs_trading_weights


class TS151PairsTradingBot(TS151Bot):
    """Pairs Trading strategy (§3.8).

    Within-sector cointegration/correlation pairs. Trades mean reversion of the log-price spread
    using pure state replay.
    """

    BOT_NAME = "ts151_PairsTradingBot"
    DEFAULT_UNIVERSE = "sp500"
    BENCHMARKS = ()
    ALLOW_SHORT = True
    LONG_ONLY = False
    MAX_GROSS = 2.0
    BACKTEST_PERIOD = "max"
    LIVE_PERIOD = "3y"
    MIN_UNIVERSE_COVERAGE = 0.8

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "formation": 252,
        "n_pairs": 10,
        "entry_z": 2.0,
        "exit_z": 0.5,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {
        "formation": [126, 252],
        "n_pairs": [5, 10],
        "entry_z": [1.5, 2.0],
        "exit_z": [0.3, 0.5],
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
        formation: int = 252,
        n_pairs: int = 10,
        entry_z: float = 2.0,
        exit_z: float = 0.5,
        **params: Any,
    ):
        super().__init__(
            interval=interval,
            period=period,
            rebalance=rebalance,
            universe=universe,
            long_only=long_only,
            park_cash=park_cash,
            formation=formation,
            n_pairs=n_pairs,
            entry_z=entry_z,
            exit_z=exit_z,
            **params,
        )

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        return compute_pairs_trading_weights(
            ctx.closes,
            formation=int(self.formation),
            n_pairs=int(self.n_pairs),
            entry_z=float(self.entry_z),
            exit_z=float(self.exit_z),
            gross=float(self.MAX_GROSS),
        )


if __name__ == "__main__":
    run_bot(TS151PairsTradingBot)
