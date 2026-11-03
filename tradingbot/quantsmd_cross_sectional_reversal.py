"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-weights-16-cross-sectional-reversal
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_16_cross_sectional_reversal.py (Apache-2.0, (c) 2026 QuantJourney)

Weekly cross-sectional reversal across the nine S&P sector ETFs. On Fridays, rank the
sectors by their trailing 21-bar return and hold the 3 weakest (the losers) at 0.5 / 3
each, so 50% of NAV is invested and the rest stays in cash. Bets on short-term
mean reversion.

Departures from source:
  * Long-only. The source is dollar-neutral and also shorts the 3 strongest sectors.
    Only the long leg (the losers) is held here, so the bot is net 50% long and NOT
    market-neutral.
  * Rebalance: the source's rebalance engine cannot be reproduced statelessly. The
    book is set on the Friday gate, and the framework's no-trade band decides what
    actually trades.

Universe: SECTORS9 (XLB, XLE, XLF, XLI, XLK, XLP, XLU, XLV, XLY).
"""

from typing import ClassVar

import numpy as np
import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import SECTORS9, closes_frame, is_weekday_bar, top_n
from tradingbot.utils.runner import run_bot

UNIVERSE = list(SECTORS9)
LOOKBACK = 21
N_LONG = 3
GROSS = 0.5
MIN_NAMES = 6
FRIDAY = 4


def compute_weights(
    closes: pd.DataFrame,
    lookback: int = LOOKBACK,
    n_long: int = N_LONG,
) -> dict[str, float] | None:
    """Return 0.5 / n_long each on the n_long weakest sectors, on Fridays.

    ``closes`` holds adjusted closes (ascending index, one column per ticker),
    history up to and including the current bar. Returns ``None`` when the last
    bar is not a Friday (no rebalance). Returns ``{}`` when there are fewer than
    ``lookback + 1`` rows or fewer than ``MIN_NAMES`` names have a valid return.
    Return is ``close[-1] / close[-1 - lookback] - 1``; the lowest returns are held.
    """
    if not is_weekday_bar(closes.index, FRIDAY):
        return None
    if len(closes) < lookback + 1:
        return {}
    past = closes.iloc[-1 - lookback]
    now = closes.iloc[-1]
    ret = (now / past - 1.0).replace([np.inf, -np.inf], np.nan).dropna()
    if len(ret) < MIN_NAMES:
        return {}
    # Negate so the weakest returns rank highest in top_n.
    losers = top_n(-ret, n_long)
    if not losers:
        return {}
    return dict.fromkeys(losers, GROSS / n_long)


class QuantsmdCrossSectionalReversalBot(Bot):
    """
    Long the weakest sector ETFs by trailing 21-bar return, rebalanced on Fridays.

    Args:
        lookback: Trading bars in the return lookback (source: 21).
        n_long: Number of weakest sectors held (the source's long leg: 3).

    Rebalances on Fridays only; every other bar returns ``None`` (book untouched).
    """

    param_grid: ClassVar[dict] = {
        "lookback": [10, 21],
    }
    # Needs more than the default 1y backtest window to be meaningful.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    def __init__(
        self,
        lookback: int = LOOKBACK,
        n_long: int = N_LONG,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdCrossSectionalReversalBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch must exceed the lookback, or returns are undefined and the
            # bot quietly sits in cash.
            period="1y",
            lookback=lookback,
            n_long=n_long,
            **kwargs,
        )
        self.lookback = lookback
        self.n_long = n_long

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float] | None:
        """Return the loser book on Fridays, ``None`` on every other bar."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes, self.lookback, self.n_long)


if __name__ == "__main__":
    run_bot(QuantsmdCrossSectionalReversalBot)
