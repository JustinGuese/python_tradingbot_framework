"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-weights-09-intraday-1h-sma-trend
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_09_intraday_1h_sma_trend.py (Apache-2.0, (c) 2026 QuantJourney)

Hourly SMA trend on SPY, QQQ and IWM. An ETF is held while its 10-bar simple
moving average is above its 30-bar one, on the latest hourly bar. Held ETFs are
equal-weighted, each capped at 25%, and whatever the cap leaves unallocated stays
in cash.

Departures from source:
- Long-only, as the framework cannot short. The source's book is followed for the
  long side; nothing is shorted.
- Warmup guard: fewer than 30 hourly rows returns ``{}``. The source has none.
"""

from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF3, closes_frame, equal_weight
from tradingbot.utils.runner import run_bot

UNIVERSE = list(ETF3)
FAST = 10
SLOW = 30
CAP = 0.25
MIN_ROWS = 30


def compute_weights(
    closes: pd.DataFrame,
    fast: int,
    slow: int,
    cap: float,
) -> dict[str, float]:
    """Equal-weight the ETFs whose fast SMA is above their slow SMA on the last bar.

    ``closes`` holds history up to and including the current bar, with one
    column per ticker. Fewer than ``max(MIN_ROWS, slow)`` rows returns ``{}``.
    A ticker is active only when both rolling means are defined on the last bar
    and the fast mean is strictly above the slow one. Each active weight is
    ``min(1/len(active), cap)``.
    """
    if len(closes) < max(MIN_ROWS, slow):
        return {}
    sma_fast = closes.rolling(fast, min_periods=fast).mean().iloc[-1]
    sma_slow = closes.rolling(slow, min_periods=slow).mean().iloc[-1]
    valid = sma_fast.notna() & sma_slow.notna()
    active = [str(t) for t in closes.columns if bool(valid[t]) and bool(sma_fast[t] > sma_slow[t])]
    return equal_weight(active, cap)


class QuantsmdIntraday1hSmaTrendBot(Bot):
    """
    Hourly SMA trend on SPY, QQQ and IWM, equal weight with a 25% cap.

    Args:
        fast: Fast simple moving average length in hourly bars.
        slow: Slow simple moving average length in hourly bars. An ETF is held
            only while its fast average is above this one.
        cap: Maximum weight for any single ETF.

    The source trades on every hourly bar. It never returns ``None``.
    """

    # A 1-year hourly backtest covers well over the 30-bar slow window.
    BACKTEST_PERIOD: ClassVar[str | None] = "1y"

    param_grid: ClassVar[dict] = {
        "fast": [5, 10, 20],
        "slow": [30, 50, 60],
    }

    def __init__(
        self,
        fast: int = FAST,
        slow: int = SLOW,
        cap: float = CAP,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdIntraday1hSmaTrendBot",
            tickers=list(UNIVERSE),
            interval="1h",
            # Live fetch: about three months of hourly bars, well above the slow
            # window, so the last bar always has a defined average.
            period="3mo",
            fast=fast,
            slow=slow,
            cap=cap,
            **kwargs,
        )
        self.fast = fast
        self.slow = slow
        self.cap = cap

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the ETF book for the current hourly bar."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes, self.fast, self.slow, self.cap)


if __name__ == "__main__":
    run_bot(QuantsmdIntraday1hSmaTrendBot)
