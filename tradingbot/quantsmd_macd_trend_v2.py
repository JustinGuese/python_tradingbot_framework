"""
Variant of quantsmd_macd_trend.

MACD trend across the nine S&P sectors, with hysteresis and a minimum holding
period. For each sector the MACD line is the 12-day EMA minus the 26-day EMA,
and its signal line is the 9-day EMA of the MACD line. The histogram (MACD minus
signal) is normalised by close, so one band works across sectors. A sector
enters once its normalised histogram is above +BAND, and leaves only when it
falls below -BAND. Once entered it must be held for at least MIN_HOLD bars
before it can flip again. Held sectors are equal-weighted, each capped at 25%,
and whatever the cap leaves unallocated stays in cash.

Departures from quantsmd_macd_trend:
- Hysteresis: the source exits the moment MACD crosses below its signal line.
  Here a sector must cross the +/- BAND thresholds on the normalised histogram,
  so a one-bar dip inside the band does not trigger a sell.
- Minimum hold: a state flip is allowed only MIN_HOLD bars after the last flip.
  The source's 14.6k trades were mostly churn that lost to costs.
- Live period is 2y, not 1y, so the hysteresis and hold state has history to
  settle before the last bar.
- Warmup guard as in the source: fewer than 35 rows returns ``{}``.
- Long-only and gross <= 1.0 are already true of the source, so nothing else
  changes.
"""

from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import SECTORS9, closes_frame, equal_weight
from tradingbot.utils.quantsmd_v2 import min_hold_state, schmitt_state
from tradingbot.utils.runner import run_bot

UNIVERSE = list(SECTORS9)
FAST = 12
SLOW = 26
SIGNAL = 9
CAP = 0.25
MIN_ROWS = 35
BAND = 0.002
MIN_HOLD = 10
REPLAY_BARS = 500  # bounds the per-bar state replay so a long backtest stays linear


def compute_weights(
    closes: pd.DataFrame,
    fast: int,
    slow: int,
    signal: int,
    band: float = BAND,
    min_hold: int = MIN_HOLD,
) -> dict[str, float]:
    """Equal-weight the sectors held by the hysteresis and minimum-hold rule.

    ``closes`` holds history up to and including the current bar, with one
    column per ticker. Fewer than ``MIN_ROWS`` rows returns ``{}``. For each
    column the MACD line is the ``fast`` EMA minus the ``slow`` EMA, and the
    signal line is the ``signal`` EMA of that MACD line, all with
    ``adjust=False``. The histogram ``macd - signal`` is divided by close. A
    Schmitt state turns on above ``+band`` and off below ``-band``. The state
    is then held by ``min_hold_state`` with ``min_hold`` bars between flips. A
    ticker is active when its held state is True on the last bar. Each active
    weight is ``min(1/len(active), CAP)``, so any clipped excess stays in cash.
    """
    if len(closes) < MIN_ROWS:
        return {}
    ema_fast = closes.ewm(span=fast, adjust=False).mean()
    ema_slow = closes.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    hist_norm = ((macd - signal_line) / closes).iloc[-REPLAY_BARS:]
    active: list[str] = []
    for ticker in closes.columns:
        raw = schmitt_state(hist_norm[ticker], enter_above=band, exit_below=-band)
        held = min_hold_state(raw, min_hold)
        if bool(held.iloc[-1]):
            active.append(str(ticker))
    return equal_weight(active, CAP)


class QuantsmdMacdTrendV2Bot(Bot):
    """
    Daily MACD trend on the nine S&P sector ETFs, with hysteresis and a minimum hold.

    Args:
        fast: Fast EMA length in daily bars for the MACD line.
        slow: Slow EMA length in daily bars for the MACD line.
        signal: EMA length in daily bars for the signal line.
        band: Hysteresis half-width on the close-normalised histogram.
        min_hold: Minimum bars between state flips for a sector.

    The book is returned on every bar and never ``None``.
    """

    param_grid: ClassVar[dict] = {
        "band": [0.001, 0.002, 0.004],
        "min_hold": [5, 10, 21],
    }

    def __init__(
        self,
        fast: int = FAST,
        slow: int = SLOW,
        signal: int = SIGNAL,
        band: float = BAND,
        min_hold: int = MIN_HOLD,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdMacdTrendV2Bot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: two years of bars, so the hysteresis and hold state
            # have settled before the last bar.
            period="2y",
            fast=fast,
            slow=slow,
            signal=signal,
            band=band,
            min_hold=min_hold,
            **kwargs,
        )
        self.fast = fast
        self.slow = slow
        self.signal = signal
        self.band = band
        self.min_hold = min_hold

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the hysteresis sector book for the current bar."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(
            closes,
            self.fast,
            self.slow,
            self.signal,
            band=self.band,
            min_hold=self.min_hold,
        )


if __name__ == "__main__":
    run_bot(QuantsmdMacdTrendV2Bot)
