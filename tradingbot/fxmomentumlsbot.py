"""
FxMomentumLSBot — Dollar-neutral long/short FX momentum on CurrencyShares ETFs.

Implements PLAN-carryblend.md S3:
- Universe: Six CurrencyShares ETFs: FXE, FXB, FXA, FXC, FXF, FXY.
- Monthly rebalance on first bar of the calendar month (or when flat live).
- Dollar-neutral long top-k / short bottom-k by trailing total return:
  close.pct_change(window).
- Ranking and sizing via signed_topk_weights from tradingbot.utils.fx_carry
  (each side gross/2, equal weight).
- ALLOW_SHORT = True, SHORTABLE = ("FXE", "FXB", "FXA", "FXC", "FXF", "FXY").
- Parameters: window=189 (grid: 63, 126, 189, 252, 378), k=3 (grid: 2, 3), gross=1.0 (grid: 1.0, 1.5).
  Defaults are the walk-forward H1 winner (beat the old 252/2 defaults on H2, t 1.70 vs 1.28); see
  docs/backtests/carryblend-2026-11.md.
- Returns {} if history has fewer than window+1 bars or fewer than 2*k valid tickers.
- No carry accrual needed on this bot (CARRY_ACCRUAL = False) since ETF price embeds carry.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

import numpy as np
import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.fx_carry import signed_topk_weights
from tradingbot.utils.live_state import live_holdings
from tradingbot.utils.quantsmd_common import closes_frame, period_rolled
from tradingbot.utils.runner import run_bot

logger = logging.getLogger(__name__)

FX_ETFS: tuple[str, ...] = ("FXE", "FXB", "FXA", "FXC", "FXF", "FXY")


def compute_fx_momentum_weights(
    closes: pd.DataFrame,
    window: int = 252,
    k: int = 2,
    gross: float = 1.0,
) -> dict[str, float]:
    """
    Compute dollar-neutral long/short weights based on trailing total return momentum.

    Ranks tickers by total return over trailing `window` bars: close.pct_change(window).
    Long top-k, short bottom-k using signed_topk_weights (each side gross/2, equal weight).
    Returns {} if history has fewer than window+1 bars or fewer than 2*k valid tickers.
    """
    if closes.empty or len(closes) < window + 1 or len(closes.columns) < 2 * k:
        return {}

    past = closes.iloc[-1 - window]
    now = closes.iloc[-1]

    # Total return over window for tickers with positive prices
    ret = (now / past - 1.0).replace([np.inf, -np.inf], np.nan).dropna()
    valid_scores = ret[(past > 0) & (now > 0)]

    if len(valid_scores) < 2 * k:
        return {}

    return signed_topk_weights(valid_scores, k=k, gross=gross)


class FxMomentumLSBot(Bot):
    """
    Dollar-neutral long/short FX momentum strategy across CurrencyShares ETFs.

    Ranks FXE, FXB, FXA, FXC, FXF, FXY by trailing total return over `window` bars.
    Holds the top-k long and bottom-k short, rebalanced monthly.
    """

    FX_ETFS: ClassVar[tuple[str, ...]] = FX_ETFS

    ALLOW_SHORT: ClassVar[bool] = True
    SHORTABLE: ClassVar[tuple[str, ...]] = FX_ETFS
    MAX_GROSS: ClassVar[float] = 2.0
    CARRY_ACCRUAL: ClassVar[bool] = False
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "window": [63, 126, 189, 252, 378],
        "k": [2, 3],
        "gross": [1.0, 1.5],
    }

    def __init__(
        self,
        window: int = 189,
        k: int = 3,
        gross: float = 1.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            "FxMomentumLSBot",
            tickers=list(self.FX_ETFS),
            interval="1d",
            period="max",
            window=window,
            k=k,
            gross=gross,
            **kwargs,
        )
        self.window = int(window)
        self.k = int(k)
        self.gross = float(gross)

    def targetWeights(self, rows: dict[str, pd.Series] | None = None) -> dict[str, float] | None:
        """
        Return target weights for the FX momentum long/short book.

        Rebalances on the first bar of each calendar month or when the portfolio is flat live.
        Returns None mid-month when holdings are already present.
        Returns {} if history has fewer than window+1 bars or fewer than 2*k tickers.
        """
        closes = closes_frame(self.datas, self.tickers)
        if closes.empty or len(closes) < self.window + 1 or len(closes.columns) < 2 * self.k:
            return {}

        holdings = live_holdings(self, rows or {})
        is_flat = holdings is not None and not holdings
        month_rolled = period_rolled(closes.index, "M")

        # Monthly rebalance gate: only rebalance on first bar of month or when flat live
        if not month_rolled and not is_flat:
            return None

        return compute_fx_momentum_weights(
            closes,
            window=self.window,
            k=self.k,
            gross=self.gross,
        )


if __name__ == "__main__":
    run_bot(FxMomentumLSBot)
