"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-orders-17-monthly-rotation-orders
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_orders_17_monthly_rotation_orders.py (Apache-2.0, (c) 2026 QuantJourney)

Monthly momentum rotation across the ETF8 universe. On the first bar of each
calendar month, rank the eight ETFs by their trailing 126-bar return and hold
the top 3 at equal weight. There is no positivity filter, so the book is always
fully invested, even when every ETF is falling.

Departures from source:
  * The source executes market orders with 3bp slippage. Here the framework's
    own execution cost model applies (ExecutionConfig), not the source's.

Universe: ETF8 (SPY, EFA, EEM, TLT, IEF, GLD, DBC, VNQ).
"""

from typing import ClassVar

import numpy as np
import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF8, closes_frame, period_rolled
from tradingbot.utils.runner import run_bot

DEFAULT_LOOKBACK = 126
DEFAULT_TOP_N = 3


def compute_weights(closes: pd.DataFrame, lookback: int, top_n: int) -> dict[str, float] | None:
    """Return equal weights over the top_n names by trailing momentum.

    ``closes`` holds adjusted closes (ascending index, one column per ticker),
    history up to and including the current bar. Returns ``None`` when the last
    bar is not the first bar of a new month, meaning "do not rebalance". Returns
    ``{}`` when there are fewer than ``lookback + 1`` rows or no ticker has a
    valid momentum. Otherwise each selected name gets ``1 / len(target)``.
    """
    if not period_rolled(closes.index, "M"):
        return None
    if len(closes) < lookback + 1:
        return {}
    past = closes.iloc[-1 - lookback]
    now = closes.iloc[-1]
    momentum = (now / past - 1.0).replace([np.inf, -np.inf], np.nan).dropna()
    target = list(momentum.nlargest(top_n).index)
    if not target:
        return {}
    weight = 1.0 / len(target)
    return dict.fromkeys(target, weight)


class QuantsmdMonthlyRotationBot(Bot):
    """
    Hold the top_n ETFs by trailing momentum, rebalanced on the first bar of each month.

    Args:
        lookback: Trading bars in the momentum lookback (source: 126).
        top_n: Number of ETFs held at equal weight (source: 3).
    """

    param_grid: ClassVar[dict] = {
        "lookback": [63, 126, 189],
        "top_n": [2, 3, 4],
    }
    # A 126-bar lookback needs more than the default 1y backtest window.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    def __init__(
        self,
        lookback: int = DEFAULT_LOOKBACK,
        top_n: int = DEFAULT_TOP_N,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdMonthlyRotationBot",
            tickers=list(ETF8),
            interval="1d",
            period="2y",
            lookback=lookback,
            top_n=top_n,
            **kwargs,
        )
        self.lookback = lookback
        self.top_n = top_n

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float] | None:
        """Delegate to compute_weights on the closes of the tradeable universe."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes, self.lookback, self.top_n)


if __name__ == "__main__":
    run_bot(QuantsmdMonthlyRotationBot)
