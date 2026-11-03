"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-weights-22-macd-trend
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_22_macd_trend.py (Apache-2.0, (c) 2026 QuantJourney)

MACD trend across the nine S&P sectors. For each sector the MACD line is the
12-day EMA minus the 26-day EMA, and its signal line is the 9-day EMA of the
MACD line. A sector is held while its MACD is above its signal line. Held
sectors are equal-weighted, each capped at 25%, and whatever the cap leaves
unallocated stays in cash.

Departures from source:
- Warmup guard: the source has none. Fewer than 35 rows returns ``{}``.
- Long-only and gross <= 1.0 are already true of the source, so nothing else
  changes.
"""

from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import SECTORS9, closes_frame, equal_weight
from tradingbot.utils.runner import run_bot

UNIVERSE = list(SECTORS9)
FAST = 12
SLOW = 26
SIGNAL = 9
CAP = 0.25
MIN_ROWS = 35


def compute_weights(
    closes: pd.DataFrame,
    fast: int,
    slow: int,
    signal: int,
) -> dict[str, float]:
    """Equal-weight the sectors whose MACD line is above its signal line.

    ``closes`` holds history up to and including the current bar, with one
    column per ticker. Fewer than ``MIN_ROWS`` rows returns ``{}``. For each
    column the MACD line is the ``fast`` EMA minus the ``slow`` EMA, and the
    signal line is the ``signal`` EMA of that MACD line, all with
    ``adjust=False``. A ticker is active when both lines are defined on the last
    bar and MACD is strictly above the signal. Each active weight is
    ``min(1/len(active), CAP)``, so any clipped excess stays in cash.
    """
    if len(closes) < MIN_ROWS:
        return {}
    ema_fast = closes.ewm(span=fast, adjust=False).mean()
    ema_slow = closes.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    macd_last = macd.iloc[-1]
    signal_last = signal_line.iloc[-1]
    valid = macd_last.notna() & signal_last.notna()
    active = [str(t) for t in closes.columns if bool(valid[t]) and bool(macd_last[t] > signal_last[t])]
    return equal_weight(active, CAP)


class QuantsmdMacdTrendBot(Bot):
    """
    Daily MACD trend on the nine S&P sector ETFs, equal weight with a 25% cap.

    Args:
        fast: Fast EMA length in daily bars for the MACD line.
        slow: Slow EMA length in daily bars for the MACD line.
        signal: EMA length in daily bars for the signal line.

    The source's daily rebalance runs every bar. It never returns ``None``.
    """

    param_grid: ClassVar[dict] = {
        "fast": [8, 12],
        "slow": [21, 26],
        "signal": [7, 9],
    }

    def __init__(
        self,
        fast: int = FAST,
        slow: int = SLOW,
        signal: int = SIGNAL,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdMacdTrendBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: a year of bars is well past the 35-row warmup and the
            # slow EMA, so every sector is evaluable on each run.
            period="1y",
            fast=fast,
            slow=slow,
            signal=signal,
            **kwargs,
        )
        self.fast = fast
        self.slow = slow
        self.signal = signal

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the sector book for the current bar."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes, self.fast, self.slow, self.signal)


if __name__ == "__main__":
    run_bot(QuantsmdMacdTrendBot)
