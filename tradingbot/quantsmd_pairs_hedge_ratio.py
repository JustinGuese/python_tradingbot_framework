"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-weights-14-pairs-hedge-ratio
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_14_pairs_hedge_ratio.py (Apache-2.0, (c) 2026 QuantJourney)

Pairs trade on EWA (Australia) versus EWC (Canada). The hedge ratio is the rolling
regression slope of log(EWA) on log(EWC). The z-score of the hedged spread opens a
position when |z| exceeds 2 and closes it when |z| falls below 0.5.

Departures from source: long-only. The source is market-neutral (long one leg,
short the other). Here the short leg is dropped, so each position is a plain 50%
long in the cheap name and this bot is no longer market-neutral.
"""

from typing import ClassVar

import numpy as np
import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import closes_frame
from tradingbot.utils.runner import run_bot

UNIVERSE = ["EWA", "EWC"]
WINDOW = 60
ENTER = 2.0
EXIT = 0.5
REPLAY_BARS = 250


def pair_state(z: pd.Series) -> int:
    """Replay the entry and exit state machine over the last ``REPLAY_BARS`` z-scores.

    Returns ``+1`` (long A), ``-1`` (long B) or ``0`` (flat). A NaN z-score resets
    the state to flat. While flat, ``z > 2`` sets ``-1`` and ``z < -2`` sets ``+1``.
    While in a position, only ``|z| < 0.5`` returns to flat, so the state never
    flips directly from ``+1`` to ``-1`` or back.
    """
    state = 0
    for value in z.iloc[-REPLAY_BARS:].to_numpy(dtype=float):
        if np.isnan(value):
            state = 0
        elif state == 0:
            if value > ENTER:
                state = -1
            elif value < -ENTER:
                state = 1
        elif abs(value) < EXIT:
            state = 0
    return state


def compute_weights(
    closes: pd.DataFrame,
    a: str,
    b: str,
    window: int = WINDOW,
) -> dict[str, float]:
    """Return the pair book for the current bar.

    ``closes`` holds history up to and including the current bar, with one column
    per ticker. Fewer than ``window + 1`` rows (61 at the default window) returns
    ``{}``. With ``la = log(a)`` and ``lb = log(b)``, the hedge ratio is
    ``beta = cov(la, lb) / var(lb)`` over ``window`` bars, the spread is
    ``la - beta * lb``, and ``z`` is its z-score over ``window`` bars. The z-score is
    computed over the whole frame before the state replay. State ``+1`` holds
    ``{a: 0.5}``, state ``-1`` holds ``{b: 0.5}``, and state ``0`` returns ``{}``.
    """
    if len(closes) < window + 1 or a not in closes or b not in closes:
        return {}
    la = np.log(closes[a])
    lb = np.log(closes[b])
    beta = la.rolling(window).cov(lb) / lb.rolling(window).var()
    spread = la - beta * lb
    z = (spread - spread.rolling(window).mean()) / spread.rolling(window).std()
    state = pair_state(z)
    if state == 1:
        return {a: 0.5}
    if state == -1:
        return {b: 0.5}
    return {}


class QuantsmdPairsHedgeRatioBot(Bot):
    """
    Long-only hedge-ratio pairs trade on EWA versus EWC.

    Args:
        window: Rolling window in daily bars for the hedge ratio and for the
            spread's mean and standard deviation.

    The source's daily rebalance runs every bar. It never returns ``None``.
    """

    # A 60-bar regression and z-score need more than the default 1y backtest window.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "window": [40, 60, 120],
    }

    def __init__(
        self,
        window: int = WINDOW,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdPairsHedgeRatioBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must exceed the window, or the hedge ratio and z-score are
            # never defined and the bot holds cash without warning.
            period="3y",
            window=window,
            **kwargs,
        )
        self.window = window

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the pair book for the current bar."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes, UNIVERSE[0], UNIVERSE[1], self.window)


if __name__ == "__main__":
    run_bot(QuantsmdPairsHedgeRatioBot)
