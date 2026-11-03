"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-weights-24-fx-cross-sectional-momentum
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_24_fx_cross_sectional_momentum.py (Apache-2.0, (c) 2026 QuantJourney)

Monthly cross-sectional momentum on four FX spot pairs. On the first bar of each
calendar month, rank the pairs by their trailing 63-bar price return and hold
the single best pair at 50% of NAV. The source also shorts the worst pair; that
leg is dropped here, so the other half of NAV stays in cash.

Departures from source:
  * Long-only: the short leg on the worst pair is dropped. The book is not
    market-neutral, and the unused half of NAV stays in cash.
  * Paper only. Prices are spot FX price returns, with no carry or swap.
  * The source's rebalance engine is replaced by the framework's no-trade band.

Universe: FX4 (EURUSD=X, GBPUSD=X, AUDUSD=X, NZDUSD=X).
"""

from typing import ClassVar

import numpy as np
import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import FX4, closes_frame, period_rolled, top_n
from tradingbot.utils.runner import run_bot

LOOKBACK = 63
MIN_NAMES = 4
LONG_WEIGHT = 0.5


def compute_weights(closes: pd.DataFrame) -> dict[str, float] | None:
    """Return the 50% long position in the best pair by trailing momentum.

    ``closes`` holds spot closes (ascending index, one column per pair), history
    up to and including the current bar. Returns ``None`` when the last bar is
    not the first bar of a new month, meaning "do not rebalance". Returns ``{}``
    when there are fewer than ``LOOKBACK + 1`` rows or fewer than ``MIN_NAMES``
    pairs have a valid score. Otherwise the top-ranked pair gets ``LONG_WEIGHT``.
    """
    if not period_rolled(closes.index, "M"):
        return None
    if len(closes) < LOOKBACK + 1:
        return {}
    score = (closes.iloc[-1] / closes.iloc[-1 - LOOKBACK] - 1.0).replace([np.inf, -np.inf], np.nan).dropna()
    if len(score) < MIN_NAMES:
        return {}
    best = top_n(score, 1)
    if not best:
        return {}
    return {best[0]: LONG_WEIGHT}


class QuantsmdFxCrossSectionalMomentumBot(Bot):
    """
    Hold the best FX pair by trailing 63-bar momentum at 50%, rebalanced monthly.

    The source ranks the pairs and shorts the worst one. Only the long leg is
    held here, so the bot is not market-neutral.
    """

    # A 63-bar lookback needs more than the default 1y backtest window.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    def __init__(self, **kwargs):
        super().__init__(
            "QuantsmdFxCrossSectionalMomentumBot",
            tickers=list(FX4),
            interval="1d",
            period="1y",
            **kwargs,
        )

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float] | None:
        """Delegate to compute_weights on the closes of the tradeable universe."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes)


if __name__ == "__main__":
    run_bot(QuantsmdFxCrossSectionalMomentumBot)
