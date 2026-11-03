"""
Variant of quantsmd_sma_daily.py: the same sector SMA book, gated by the S&P 500 trend.

Daily SMA trend across the nine S&P sectors. A sector is held while its fast
simple moving average is above its slow one. Held sectors are equal-weighted,
each capped at 25%, and whatever the cap leaves unallocated stays in cash.
The whole book is then multiplied by a regime factor: 1.0 while SPY closes
above its 200-bar simple moving average, and ``off_scale`` otherwise.

Departures from quantsmd_sma_daily:
  * SPY is loaded as a benchmark (``benchmark_tickers=["SPY"]``). It is never
    traded and never part of the sector book; it only feeds the regime gate.
  * The gate scales the sector book by ``regime_scale`` through ``scale_book``.
    With ``off_scale=0.0`` a SPY downtrend moves the book to all cash.
  * A bar where the original returns no book (``None``) is passed through
    unchanged, so the gate never forces a rebalance on its own. Every original
    book here is non-``None``, so the gate applies on every bar.
"""

from typing import ClassVar

import pandas as pd

from tradingbot import quantsmd_sma_daily as base
from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import SECTORS9, closes_frame
from tradingbot.utils.quantsmd_v2 import regime_scale, scale_book
from tradingbot.utils.runner import run_bot

UNIVERSE = list(SECTORS9)
BENCHMARK = "SPY"
FAST = base.FAST
SLOW = base.SLOW
CAP = base.CAP
REGIME_WINDOW = 200
OFF_SCALE = 0.0


def compute_weights(
    closes: pd.DataFrame,
    fast: int,
    slow: int,
    cap: float,
    off_scale: float = OFF_SCALE,
) -> dict[str, float]:
    """Return the original sector book, scaled by the SPY regime factor.

    ``closes`` holds history up to and including the current bar, one column
    per ticker. ``SPY``, when present, is used only for the regime gate and is
    dropped before the sector book is built. Fewer than ``slow`` sector rows
    gives ``{}`` from the original. A missing or too-short SPY history fails
    open, so the book is not scaled.
    """
    sectors = closes.drop(columns=[BENCHMARK], errors="ignore")
    book = base.compute_weights(sectors, fast, slow, cap)
    factor = regime_scale(closes, ref=BENCHMARK, window=REGIME_WINDOW, off_scale=off_scale)
    return scale_book(book, factor)


class QuantsmdSmaDailyGatedBot(Bot):
    """
    Daily sector SMA book on the nine S&P sectors, gated by SPY's 200-bar SMA.

    Args:
        fast: Fast simple moving average length in daily bars.
        slow: Slow simple moving average length in daily bars.
        cap: Maximum weight for any single sector.
        off_scale: Multiplier on the whole book while SPY is at or below its
            200-bar SMA. 0.0 moves to cash; 0.5 halves the book.

    Like the original, it never returns ``None``.
    """

    # A 200-bar SMA needs more than the default 1y backtest window.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "off_scale": [0.0, 0.5],
    }

    def __init__(
        self,
        fast: int = FAST,
        slow: int = SLOW,
        cap: float = CAP,
        off_scale: float = OFF_SCALE,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdSmaDailyGatedBot",
            tickers=[*UNIVERSE, BENCHMARK],
            benchmark_tickers=[BENCHMARK],
            interval="1d",
            # Live fetch: must exceed both the slow sector window and the
            # 200-bar regime window, or the gate and the book stay flat.
            period="2y",
            fast=fast,
            slow=slow,
            cap=cap,
            off_scale=off_scale,
            **kwargs,
        )
        self.fast = fast
        self.slow = slow
        self.cap = cap
        self.off_scale = off_scale

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the regime-gated sector book for the current bar."""
        closes = closes_frame(self.datas, self.tickers)
        return compute_weights(closes, self.fast, self.slow, self.cap, self.off_scale)


if __name__ == "__main__":
    run_bot(QuantsmdSmaDailyGatedBot)
