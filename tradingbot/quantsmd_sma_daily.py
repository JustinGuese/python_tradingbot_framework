"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-weights-01-sma-daily
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_01_sma_daily.py (Apache-2.0, (c) 2026 QuantJourney)

Daily SMA trend across the nine S&P sectors. A sector is held while its fast
simple moving average is above its slow one. Held sectors are equal-weighted,
each capped at 25%, and whatever the cap leaves unallocated stays in cash.

Departures from source: none. The source weights are already long-only with
no leverage, so this bot follows them as written.
"""

from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import SECTORS9, closes_frame, equal_weight
from tradingbot.utils.runner import run_bot

UNIVERSE = list(SECTORS9)
FAST = 50
SLOW = 200
CAP = 0.25


def compute_weights(
    closes: pd.DataFrame,
    fast: int,
    slow: int,
    cap: float,
) -> dict[str, float]:
    """Equal-weight the sectors whose fast SMA is above their slow SMA.

    ``closes`` holds history up to and including the current bar, with one
    column per ticker. Fewer than ``slow`` rows returns ``{}``. A ticker is
    active only when both averages are defined on the last bar and the fast
    average is strictly above the slow one. Each active weight is
    ``min(1/len(active), cap)``, so any clipped excess stays in cash.
    """
    if len(closes) < slow:
        return {}
    sma_fast = closes.rolling(fast, min_periods=fast).mean().iloc[-1]
    sma_slow = closes.rolling(slow, min_periods=slow).mean().iloc[-1]
    valid = sma_fast.notna() & sma_slow.notna()
    active = [str(t) for t in closes.columns if bool(valid[t]) and bool(sma_fast[t] > sma_slow[t])]
    return equal_weight(active, cap)


class QuantsmdSmaDailyBot(Bot):
    """
    Daily SMA trend on the nine S&P sector ETFs, equal weight with a 25% cap.

    Args:
        fast: Fast simple moving average length in daily bars.
        slow: Slow simple moving average length in daily bars. A sector is
            held only while its fast average is above this one.
        cap: Maximum weight for any single sector.

    The source's daily rebalance runs every bar. It never returns ``None``.
    """

    # A 200-bar SMA needs more than the default 1y backtest window.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "fast": [20, 50, 100],
        "slow": [150, 200, 250],
    }

    def __init__(
        self,
        fast: int = FAST,
        slow: int = SLOW,
        cap: float = CAP,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdSmaDailyBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must exceed the slow window, or no sector is ever
            # evaluable and the bot holds cash without warning.
            period="2y",
            fast=fast,
            slow=slow,
            cap=cap,
            **kwargs,
        )
        self.fast = fast
        self.slow = slow
        self.cap = cap

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the sector book for the current bar."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes, self.fast, self.slow, self.cap)


if __name__ == "__main__":
    run_bot(QuantsmdSmaDailyBot)
