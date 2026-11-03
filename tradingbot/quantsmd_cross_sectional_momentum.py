"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-weights-15-cross-sectional-momentum
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_15_cross_sectional_momentum.py (Apache-2.0, (c) 2026 QuantJourney)

Monthly cross-sectional momentum across the nine S&P sector ETFs. On the first bar
of each calendar month, rank the sectors by their trailing 252-bar return and hold
the top 3 at 0.5 / 3 each, so 50% of NAV is invested and the rest stays in cash.

Departures from source:
  * Long-only. The source is dollar-neutral and also shorts the 3 weakest sectors.
    Only the long leg is held here, so the bot is net 50% long and NOT
    market-neutral.
  * Rebalance: the source's rebalance engine cannot be reproduced statelessly. The
    book is set on the monthly gate, and the framework's no-trade band decides
    what actually trades.

Universe: SECTORS9 (XLB, XLE, XLF, XLI, XLK, XLP, XLU, XLV, XLY).
"""

from typing import ClassVar

import numpy as np
import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import SECTORS9, closes_frame, period_rolled, top_n
from tradingbot.utils.runner import run_bot

UNIVERSE = list(SECTORS9)
LOOKBACK = 252
N_LONG = 3
GROSS = 0.5
MIN_NAMES = 6


def compute_weights(
    closes: pd.DataFrame,
    lookback: int = LOOKBACK,
    n_long: int = N_LONG,
) -> dict[str, float] | None:
    """Return 0.5 / n_long each on the n_long strongest sectors, on month-first bars.

    ``closes`` holds adjusted closes (ascending index, one column per ticker),
    history up to and including the current bar. Returns ``None`` when the last
    bar is not the first bar of a new month (no rebalance). Returns ``{}`` when
    there are fewer than ``lookback + 1`` rows or fewer than ``MIN_NAMES`` names
    have a valid momentum. Momentum is ``close[-1] / close[-1 - lookback] - 1``.
    """
    if not period_rolled(closes.index, "M"):
        return None
    if len(closes) < lookback + 1:
        return {}
    past = closes.iloc[-1 - lookback]
    now = closes.iloc[-1]
    momentum = (now / past - 1.0).replace([np.inf, -np.inf], np.nan).dropna()
    if len(momentum) < MIN_NAMES:
        return {}
    picks = top_n(momentum, n_long)
    if not picks:
        return {}
    return dict.fromkeys(picks, GROSS / n_long)


class QuantsmdCrossSectionalMomentumBot(Bot):
    """
    Long the strongest sector ETFs by trailing 252-bar return, rebalanced monthly.

    Args:
        lookback: Trading bars in the momentum lookback (source: 252).
        n_long: Number of sectors held (the source's long leg: 3).

    Rebalances on the first bar of each month; every other bar returns ``None``.
    """

    param_grid: ClassVar[dict] = {
        "lookback": [126, 252],
    }
    # A 252-bar lookback needs more than the default 1y backtest window.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    def __init__(
        self,
        lookback: int = LOOKBACK,
        n_long: int = N_LONG,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdCrossSectionalMomentumBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch must exceed the lookback, or momentum is undefined and the
            # bot quietly sits in cash.
            period="3y",
            lookback=lookback,
            n_long=n_long,
            **kwargs,
        )
        self.lookback = lookback
        self.n_long = n_long

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float] | None:
        """Return the momentum book on month-first bars, ``None`` otherwise."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes, self.lookback, self.n_long)


if __name__ == "__main__":
    run_bot(QuantsmdCrossSectionalMomentumBot)
