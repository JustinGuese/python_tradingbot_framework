"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-weights-02-monthly-drift-etf
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_02_monthly_drift_etf.py (Apache-2.0, (c) 2026 QuantJourney)

Monthly trend rotation across eight ETFs (SPY, EFA, EEM, TLT, IEF, GLD, DBC, VNQ).
An ETF is held while its 50-bar simple moving average is above its 200-bar one.
Held ETFs are equal-weighted, each capped at 30%, and whatever the cap leaves
unallocated stays in cash. The book is only evaluated on the first bar of each
calendar month; every other bar returns ``None`` (no rebalance).

Departures from source:
- The source's 5% drift band is replaced by the framework's own no-trade band.
"""

from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF8, closes_frame, equal_weight, period_rolled
from tradingbot.utils.runner import run_bot

UNIVERSE = list(ETF8)
FAST = 50
SLOW = 200
CAP = 0.30


def compute_weights(
    closes: pd.DataFrame,
    fast: int,
    slow: int,
    cap: float,
) -> dict[str, float] | None:
    """Equal-weight the ETFs whose fast SMA is above their slow SMA, on month starts.

    ``closes`` holds history up to and including the current bar, with one
    column per ticker. Returns ``None`` unless the last bar starts a new
    calendar month. Fewer than ``slow`` rows returns ``{}``. A ticker is active
    only when both averages are defined on the last bar and the fast average is
    strictly above the slow one. Each active weight is ``min(1/len(active), cap)``,
    so any clipped excess stays in cash.
    """
    if not period_rolled(closes.index, "M"):
        return None
    if len(closes) < slow:
        return {}
    sma_fast = closes.rolling(fast, min_periods=fast).mean().iloc[-1]
    sma_slow = closes.rolling(slow, min_periods=slow).mean().iloc[-1]
    valid = sma_fast.notna() & sma_slow.notna()
    active = [str(t) for t in closes.columns if bool(valid[t]) and bool(sma_fast[t] > sma_slow[t])]
    return equal_weight(active, cap)


class QuantsmdMonthlyDriftEtfBot(Bot):
    """
    Monthly SMA(50/200) trend rotation on eight ETFs, equal weight with a 30% cap.

    Args:
        fast: Fast simple moving average length in daily bars.
        slow: Slow simple moving average length in daily bars. An ETF is held
            only while its fast average is above this one.
        cap: Maximum weight for any single ETF.

    Rebalances on the first bar of each month and returns ``None`` otherwise.
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
            "QuantsmdMonthlyDriftEtfBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must exceed the slow window, or no ETF is ever
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

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float] | None:
        """Return the ETF book on a month start, ``None`` on every other bar."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes, self.fast, self.slow, self.cap)


if __name__ == "__main__":
    run_bot(QuantsmdMonthlyDriftEtfBot)
