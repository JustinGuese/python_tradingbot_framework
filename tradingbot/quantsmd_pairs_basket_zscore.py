"""
Variant of tradingbot/quantsmd_pairs_ratio_zscore.py.

Long-only log-ratio z-score pairs trades across a basket of eight classic pairs.
Each pair runs the original KO/PEP rule: the spread is ``log(a) - log(b)``, its
z-score is taken over a rolling window, a position opens when ``|z|`` exceeds 2
and closes when it falls below 0.5. When a pair is in a position only its cheap
leg is held, at ``1/len(PAIRS)`` of NAV, so the whole basket is at most fully
invested.

Departures from quantsmd_pairs_ratio_zscore.py:
- Eight pairs instead of one (KO/PEP, XOM/CVX, JPM/BAC, HD/LOW, MA/V, UPS/FDX,
  MCD/YUM, WMT/COST), each with an equal share of NAV. A single pair is a
  hindsight pick; the basket tests whether the rule holds across pairs.
- Each pair's sizing is 1/len(PAIRS) of NAV instead of 0.5, so gross exposure
  is at most 1.0 across the whole basket.
- The per-pair signal is the original's ``compute_weights`` helper, reused
  unchanged.
- Long-only, as the original.
"""

from typing import ClassVar

import pandas as pd

from tradingbot.quantsmd_pairs_ratio_zscore import WINDOW
from tradingbot.quantsmd_pairs_ratio_zscore import compute_weights as _single_pair_weights
from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import closes_frame
from tradingbot.utils.runner import run_bot

PAIRS: list[tuple[str, str]] = [
    ("KO", "PEP"),
    ("XOM", "CVX"),
    ("JPM", "BAC"),
    ("HD", "LOW"),
    ("MA", "V"),
    ("UPS", "FDX"),
    ("MCD", "YUM"),
    ("WMT", "COST"),
]
UNIVERSE = [ticker for pair in PAIRS for ticker in pair]


def compute_weights(closes: pd.DataFrame, window: int = WINDOW) -> dict[str, float]:
    """Return the basket book for the current bar.

    ``closes`` holds history up to and including the current bar, with one column
    per ticker. Each pair is evaluated with the original single-pair rule. A pair
    that is in a position contributes its cheap leg at ``1/len(PAIRS)``; a pair
    that is flat contributes nothing. Pairs are disjoint, so no ticker is counted
    twice, and the total is at most 1.0. A pair with a missing ticker, or fewer
    than ``window + 1`` rows, contributes nothing.
    """
    book: dict[str, float] = {}
    share = 1.0 / len(PAIRS)
    for a, b in PAIRS:
        for ticker in _single_pair_weights(closes, a, b, window):
            book[ticker] = share
    return book


class QuantsmdPairsBasketZscoreBot(Bot):
    """
    Long-only log-ratio z-score pairs basket over eight classic pairs.

    Args:
        window: Rolling window in daily bars for each spread's mean and standard
            deviation.
    """

    # A 60-bar z-score needs more than the default 1y backtest window.
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
            "QuantsmdPairsBasketZscoreBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must exceed the window, or the z-scores are never defined
            # and the bot holds cash without warning.
            period="3y",
            window=window,
            **kwargs,
        )
        self.window = window

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the basket book for the current bar."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes, self.window)


if __name__ == "__main__":
    run_bot(QuantsmdPairsBasketZscoreBot)
