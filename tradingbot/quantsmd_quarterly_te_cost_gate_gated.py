"""
Variant of quantsmd_quarterly_te_cost_gate.py: the same rotation, gated by the SPY trend.

Quarterly momentum rotation across the ETF8 universe with a tracking-error and
turnover gate. The signal is the top 3 ETFs by trailing 126-bar return, held at
equal weight capped at 40%. The book rebalances on the first bar of each
calendar quarter, and early when the held book's tracking error against SPY
breaches the threshold, within the turnover budget. On a rebalance bar the
book is then multiplied by a regime factor: 1.0 while SPY closes above its
200-bar simple moving average, and ``off_scale`` otherwise.

Departures from quantsmd_quarterly_te_cost_gate:
  * The regime factor scales the rebalance book through ``scale_book``. With
    ``off_scale=0.0`` a rebalance taken while SPY is in a downtrend moves to cash.
  * A bar where the original returns ``None`` (no rebalance) is passed through
    unchanged. The gate never forces a rebalance. A regime change in the middle
    of a quarter is therefore picked up only at the next rebalance trigger.
  * The original's gate state tracks the unscaled target. Turnover and tracking
    error are measured on that target, not on the cash the regime gate may hold.

Universe: ETF8 (SPY, EFA, EEM, TLT, IEF, GLD, DBC, VNQ). SPY is the benchmark
and also feeds the regime gate.
"""

from typing import Any, ClassVar

import pandas as pd

from tradingbot import quantsmd_quarterly_te_cost_gate as base
from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF8, closes_frame
from tradingbot.utils.quantsmd_v2 import regime_scale, scale_book
from tradingbot.utils.runner import run_bot

UNIVERSE = list(ETF8)
BENCHMARK = "SPY"
LOOKBACK = base.LOOKBACK
TOP_N = base.TOP_N
CAP = base.CAP
TE_THRESHOLD = base.TE_THRESHOLD
TE_WINDOW = base.TE_WINDOW
MAX_ANNUAL_TURNOVER = base.MAX_ANNUAL_TURNOVER
REGIME_WINDOW = 200
OFF_SCALE = 0.0


def compute_weights(
    closes: pd.DataFrame,
    lookback: int,
    top_n: int,
    cap: float,
    te_threshold: float,
    te_window: int,
    max_annual_turnover: float,
    state: dict[str, Any],
    off_scale: float = OFF_SCALE,
) -> dict[str, float] | None:
    """Return the regime-gated rebalance book, or ``None`` when no rebalance is due.

    The original ``compute_weights`` decides whether a rebalance is due and
    mutates ``state`` as it does. ``None`` from it is returned unchanged.
    Otherwise its book is multiplied by ``regime_scale`` of ``SPY`` in
    ``closes``. ``SPY`` must be a column; a missing or too-short SPY history
    fails open, so the book is not scaled.
    """
    book = base.compute_weights(
        closes,
        lookback,
        top_n,
        cap,
        te_threshold,
        te_window,
        max_annual_turnover,
        state,
    )
    if book is None:
        return None
    factor = regime_scale(closes, ref=BENCHMARK, window=REGIME_WINDOW, off_scale=off_scale)
    return scale_book(book, factor)


class QuantsmdQuarterlyTeCostGateGatedBot(Bot):
    """
    Quarterly top-3 momentum on ETF8, rebalanced early on tracking-error breach,
    with the rebalance book gated by SPY's 200-bar SMA.

    Args:
        lookback: Trailing bars for the momentum ranking.
        top_n: Number of ETFs held.
        cap: Maximum weight for any single ETF.
        te_threshold: Annualised tracking error vs SPY that forces an early rebalance.
        te_window: Bars used to measure tracking error.
        max_annual_turnover: Cap on one-way turnover summed over the last 365 days.
        off_scale: Multiplier on the rebalance book while SPY is at or below its
            200-bar SMA. 0.0 moves to cash; 0.5 halves the book.

    As in the original, the gate's state lives on the instance. Each live
    CronJob run starts with no state, so its first call always builds the book.
    """

    # A 126-bar lookback with quarterly rebalances needs more than the default 1y.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "off_scale": [0.0, 0.5],
    }

    def __init__(
        self,
        lookback: int = LOOKBACK,
        top_n: int = TOP_N,
        cap: float = CAP,
        te_threshold: float = TE_THRESHOLD,
        te_window: int = TE_WINDOW,
        max_annual_turnover: float = MAX_ANNUAL_TURNOVER,
        off_scale: float = OFF_SCALE,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdQuarterlyTeCostGateGatedBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must exceed the momentum lookback and the regime window.
            period="2y",
            lookback=lookback,
            top_n=top_n,
            cap=cap,
            te_threshold=te_threshold,
            te_window=te_window,
            max_annual_turnover=max_annual_turnover,
            off_scale=off_scale,
            **kwargs,
        )
        self.lookback = lookback
        self.top_n = top_n
        self.cap = cap
        self.te_threshold = te_threshold
        self.te_window = te_window
        self.max_annual_turnover = max_annual_turnover
        self.off_scale = off_scale
        self._state: dict[str, Any] = {"last_target": None, "turnover": []}

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float] | None:
        """Return the gated book on a rebalance bar, or ``None`` to leave it untouched."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(
            closes,
            self.lookback,
            self.top_n,
            self.cap,
            self.te_threshold,
            self.te_window,
            self.max_annual_turnover,
            self._state,
            self.off_scale,
        )


if __name__ == "__main__":
    run_bot(QuantsmdQuarterlyTeCostGateGatedBot)
