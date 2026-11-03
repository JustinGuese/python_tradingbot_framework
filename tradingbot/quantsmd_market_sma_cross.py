"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-orders-01-market-sma-cross
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_orders_01_market_sma_cross.py (Apache-2.0, (c) 2026 QuantJourney)

Market-order SMA crossover on SPY, QQQ and IWM. A name is held at 18% of NAV
while its 20-day SMA is above its 50-day SMA, and is out otherwise. The source
enters and exits with market orders on each cross. The same signal is used by
QuantsmdSignalChangeRotationOrdersBot (O18). The two differ only in the ``None``
gate: this bot returns the book on every bar, and O18 returns it only on flips.

Departures from source:
- No resting orders: the source only uses market orders, which act at the bar's
  close. The target book is recomputed on every bar and nothing is left at a
  broker between runs, so no replay is needed.
- Fixed fraction sizing: the source holds a fixed share count. Here each held
  name targets 18% of NAV, and the framework's no-trade band avoids churn.
"""

from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF3, closes_frame
from tradingbot.utils.runner import run_bot

UNIVERSE = list(ETF3)
FAST = 20
SLOW = 50
SIZE = 0.18


def sma_signal(closes: pd.DataFrame, fast: int, slow: int) -> pd.DataFrame:
    """Per-bar SMA crossover signal: 1.0 when fast > slow, 0.0 when not, NaN when undefined.

    A bar is undefined for a ticker unless both averages are defined on that
    bar, using ``min_periods`` equal to each window.
    """
    sma_fast = closes.rolling(fast, min_periods=fast).mean()
    sma_slow = closes.rolling(slow, min_periods=slow).mean()
    defined = sma_fast.notna() & sma_slow.notna()
    return (sma_fast > sma_slow).astype(float).where(defined)


def compute_weights(
    closes: pd.DataFrame,
    fast: int,
    slow: int,
    size: float,
) -> dict[str, float]:
    """Hold ``size`` in each name whose fast SMA is above its slow SMA on the last bar.

    ``closes`` holds history up to and including the current bar, with one
    column per ticker. Fewer than ``slow`` rows returns ``{}``. A ticker is held
    only when both averages are defined on the last bar and the fast average is
    strictly above the slow one. Names that are not held are omitted.
    """
    if len(closes) < slow:
        return {}
    last = sma_signal(closes, fast, slow).iloc[-1]
    return {str(t): size for t in closes.columns if last[t] == 1.0}


class QuantsmdMarketSmaCrossBot(Bot):
    """
    Market-order SMA crossover on SPY, QQQ and IWM, 18% of NAV per held name.

    Args:
        fast: Fast simple moving average length in daily bars.
        slow: Slow simple moving average length in daily bars. A name is held
            only while its fast average is above this one.
        size: Fixed fraction of NAV for each held name.

    The source acts on crosses only. Here the book is returned on every bar
    (never ``None``), so a held name stays at its size until the cross reverses.
    """

    # A 50-bar SMA needs more than the default 1y backtest window.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "fast": [10, 20, 50],
        "slow": [50, 100, 200],
    }

    def __init__(
        self,
        fast: int = FAST,
        slow: int = SLOW,
        size: float = SIZE,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdMarketSmaCrossBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must exceed the slow window, or no name is ever
            # evaluable and the bot holds cash without warning.
            period="2y",
            fast=fast,
            slow=slow,
            size=size,
            **kwargs,
        )
        self.fast = fast
        self.slow = slow
        self.size = size

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the SMA-cross book for the current bar."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes, self.fast, self.slow, self.size)


if __name__ == "__main__":
    run_bot(QuantsmdMarketSmaCrossBot)
