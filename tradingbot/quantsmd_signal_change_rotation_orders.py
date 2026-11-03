"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-orders-18-signal-change-rotation-orders
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_orders_18_signal_change_rotation_orders.py (Apache-2.0, (c) 2026 QuantJourney)

Signal-change rotation on SPY, QQQ and IWM. The book is the same as
QuantsmdMarketSmaCrossBot (O01): 18% of NAV in each name whose 20-day SMA is
above its 50-day SMA. The source only trades on a signal flip, so this bot
returns the full book only on a bar where at least one name's signal flipped
versus the bar before, and ``None`` (no rebalance) on every other bar.

Departures from source:
- No resting orders: the source only uses market orders, which act at the bar's
  close. The book is recomputed on a flip bar and nothing is left at a broker
  between runs, so no replay is needed.
- Fixed fraction sizing: the source holds a fixed share count. Here each held
  name targets 18% of NAV, and the framework's no-trade band avoids churn.
- Flip gate: the book is only returned on a flip bar. A CronJob that misses a
  run on a flip bar leaves the book stale until the next flip.
"""

from typing import ClassVar

import pandas as pd

from tradingbot.quantsmd_market_sma_cross import FAST, SIZE, SLOW, sma_signal
from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF3, closes_frame
from tradingbot.utils.runner import run_bot

UNIVERSE = list(ETF3)


def compute_weights(
    closes: pd.DataFrame,
    fast: int,
    slow: int,
    size: float,
) -> dict[str, float] | None:
    """Return the SMA-cross book on a flip bar, and ``None`` otherwise.

    ``closes`` holds history up to and including the current bar, with one
    column per ticker. Fewer than ``slow + 1`` rows returns ``{}``, because a
    flip needs the signal to be defined on the last two bars. A flip is a name
    whose signal on the last bar differs from its signal on the bar before,
    with both defined. When no name flipped, returns ``None``. When at least one
    did, returns the same book as ``QuantsmdMarketSmaCrossBot``: ``size`` for
    each name held on the last bar.
    """
    if len(closes) < slow + 1:
        return {}
    signal = sma_signal(closes, fast, slow)
    last, prev = signal.iloc[-1], signal.iloc[-2]
    flipped = [t for t in closes.columns if pd.notna(last[t]) and pd.notna(prev[t]) and last[t] != prev[t]]
    if not flipped:
        return None
    return {str(t): size for t in closes.columns if last[t] == 1.0}


class QuantsmdSignalChangeRotationOrdersBot(Bot):
    """
    SMA-cross rotation on SPY, QQQ and IWM, rebalanced only on a signal flip.

    Args:
        fast: Fast simple moving average length in daily bars.
        slow: Slow simple moving average length in daily bars.
        size: Fixed fraction of NAV for each held name.

    Returns ``None`` on every bar without a flip, so the book is left as it is.
    On a flip bar it returns the full book. A missed run on a flip bar leaves the
    book stale until the next flip.
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
            "QuantsmdSignalChangeRotationOrdersBot",
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

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float] | None:
        """Return the book on a flip bar, or ``None`` to leave the book unchanged."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes, self.fast, self.slow, self.size)


if __name__ == "__main__":
    run_bot(QuantsmdSignalChangeRotationOrdersBot)
