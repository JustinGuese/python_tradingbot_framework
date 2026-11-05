"""
Variant of quantsmd_sma_daily_gated.py: idle cash parked in SHV and a configurable regime window.

Daily SMA trend across the nine S&P sectors. A sector is held while its fast
simple moving average is above its slow one. Held sectors are equal-weighted,
each capped at 25%, and whatever the cap leaves unallocated is parked in a cash
yield instrument (SHV by default) instead of remaining in cash at 0% yield.
The whole sector book is scaled by a regime factor: 1.0 while SPY closes above
its ``regime_window`` simple moving average, and ``off_scale`` otherwise. When
gated off or partially off, the residual weight is parked in cash yield.

Departures from quantsmd_sma_daily_gated:
  * Idle cash is parked in SHV (short-term Treasury ETF) via park_cash, earning
    cash yield instead of 0% in paper trading. SHV history starts in 2007, so
    backtests covering SHV begin in 2007.
  * The regime gate window is a parameter (``regime_window``, default 200 bars),
    tuned across [100, 150, 200, 250] in ``param_grid``.
  * SHV is declared in the tradeable universe ([*SECTORS9, "SPY", "SHV"]),
    while SPY remains a non-traded benchmark ticker.
"""

from typing import ClassVar

import pandas as pd

from tradingbot import quantsmd_sma_daily as base
from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import SECTORS9, closes_frame
from tradingbot.utils.quantsmd_v2 import park_cash, regime_scale, scale_book
from tradingbot.utils.runner import run_bot

UNIVERSE = list(SECTORS9)
BENCHMARK = "SPY"
CASH_SYMBOL = "SHV"
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
    regime_window: int = REGIME_WINDOW,
    cash_symbol: str | None = CASH_SYMBOL,
) -> dict[str, float]:
    """Return the original sector book, gated by SPY, with idle cash in ``cash_symbol``.

    ``closes`` holds history up to and including the current bar, one column
    per ticker. ``SPY`` and ``SHV`` (or ``cash_symbol``) are dropped from the
    sector frame before the sector book is built. Fewer than ``slow`` sector
    rows gives ``{}``. A missing or too-short SPY history fails open, so the
    book is not scaled. If ``cash_symbol`` is truthy, any residual unallocated
    weight is parked in that symbol via ``park_cash``.
    """
    drop_cols = {BENCHMARK, CASH_SYMBOL}
    if cash_symbol:
        drop_cols.add(cash_symbol)
    sectors = closes.drop(columns=list(drop_cols), errors="ignore")
    if len(sectors) < slow:
        return {}
    book = base.compute_weights(sectors, fast, slow, cap)
    factor = regime_scale(closes, ref=BENCHMARK, window=regime_window, off_scale=off_scale)
    gated = scale_book(book, factor)
    if cash_symbol:
        return park_cash(gated, cash_symbol)
    return gated


class QuantsmdSmaDailyGatedV3Bot(Bot):
    """
    Daily sector SMA book on the nine S&P sectors, gated by SPY's SMA, idle cash in SHV.

    Args:
        fast: Fast simple moving average length in daily bars.
        slow: Slow simple moving average length in daily bars.
        cap: Maximum weight for any single sector.
        off_scale: Multiplier on the whole book while SPY is at or below its
            regime SMA. 0.0 moves to cash; 0.5 halves the book.
        regime_window: Window in daily bars for the SPY regime SMA.
        cash_symbol: Symbol to park idle cash into, or None for uninvested cash.
    """

    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "regime_window": [100, 150, 200, 250],
        "off_scale": [0.0, 0.5],
    }

    def __init__(
        self,
        fast: int = FAST,
        slow: int = SLOW,
        cap: float = CAP,
        off_scale: float = OFF_SCALE,
        regime_window: int = REGIME_WINDOW,
        cash_symbol: str | None = CASH_SYMBOL,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdSmaDailyGatedV3Bot",
            tickers=[*UNIVERSE, BENCHMARK, CASH_SYMBOL],
            benchmark_tickers=[BENCHMARK],
            interval="1d",
            period="2y",
            fast=fast,
            slow=slow,
            cap=cap,
            off_scale=off_scale,
            regime_window=regime_window,
            cash_symbol=cash_symbol,
            **kwargs,
        )
        self.fast = fast
        self.slow = slow
        self.cap = cap
        self.off_scale = off_scale
        self.regime_window = regime_window
        self.cash_symbol = cash_symbol

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the regime-gated sector book with cash parked for the current bar."""
        closes = closes_frame(self.datas, self.tickers)
        return compute_weights(
            closes,
            self.fast,
            self.slow,
            self.cap,
            off_scale=self.off_scale,
            regime_window=self.regime_window,
            cash_symbol=self.cash_symbol,
        )


if __name__ == "__main__":
    run_bot(QuantsmdSmaDailyGatedV3Bot)
