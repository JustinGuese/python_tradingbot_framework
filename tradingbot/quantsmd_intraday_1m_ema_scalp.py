"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-weights-08-intraday-1m-ema-scalp
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_08_intraday_1m_ema_scalp.py (Apache-2.0, (c) 2026 QuantJourney)

Intraday 1-minute EMA scalp on SPY, QQQ and IWM. An ETF is held while its fast
exponential moving average is above its slow one, on the latest 1-minute bar.
Held ETFs are equal-weighted, each capped at 34%.

Departures from source:
- Long-only, as the framework cannot short. The source's book is followed for the
  long side; nothing is shorted.
- Scheduled every 5 minutes (open question D2 in PLAN.md), not on every bar. Each
  run evaluates the last 1-minute bar, so a run can miss intermediate crossovers.
- Warning: yfinance serves about 7 days of 1-minute bars, so a backtest says almost
  nothing about this strategy. The daily-cost model also ignores 1-minute trading
  costs (spread and slippage per trade), which matter most for a scalp.
"""

from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF3, closes_frame, equal_weight
from tradingbot.utils.runner import run_bot

UNIVERSE = list(ETF3)
FAST = 9
SLOW = 21
CAP = 0.34
MIN_ROWS = 30


def compute_weights(
    closes: pd.DataFrame,
    fast: int,
    slow: int,
    cap: float,
) -> dict[str, float]:
    """Equal-weight the ETFs whose fast EMA is above their slow EMA on the last bar.

    ``closes`` holds history up to and including the current bar, with one
    column per ticker. Fewer than ``max(MIN_ROWS, slow)`` rows returns ``{}``.
    Averages use ``ewm(span=n, adjust=False)``. A ticker is active only when both
    averages are defined on the last bar and the fast average is strictly above
    the slow one. Each active weight is ``min(1/len(active), cap)``.
    """
    if len(closes) < max(MIN_ROWS, slow):
        return {}
    ema_fast = closes.ewm(span=fast, adjust=False).mean().iloc[-1]
    ema_slow = closes.ewm(span=slow, adjust=False).mean().iloc[-1]
    valid = ema_fast.notna() & ema_slow.notna()
    active = [str(t) for t in closes.columns if bool(valid[t]) and bool(ema_fast[t] > ema_slow[t])]
    return equal_weight(active, cap)


class QuantsmdIntraday1mEmaScalpBot(Bot):
    """
    Intraday 1-minute EMA scalp on SPY, QQQ and IWM, equal weight with a 34% cap.

    Args:
        fast: Fast EMA span in 1-minute bars.
        slow: Slow EMA span in 1-minute bars. An ETF is held only while its fast
            average is above this one.
        cap: Maximum weight for any single ETF.

    The source trades on every 1-minute bar. It never returns ``None``. Paper only.
    """

    # yfinance serves about 7 days of 1-minute bars, so the backtest is short.
    BACKTEST_PERIOD: ClassVar[str | None] = "7d"

    param_grid: ClassVar[dict] = {
        "fast": [5, 9, 12],
        "slow": [21, 26, 34],
    }

    def __init__(
        self,
        fast: int = FAST,
        slow: int = SLOW,
        cap: float = CAP,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdIntraday1mEmaScalpBot",
            tickers=list(UNIVERSE),
            interval="1m",
            # Live fetch: about 5 trading days of 1-minute bars, well above the
            # slow window, so the last bar always has a defined average.
            period="5d",
            fast=fast,
            slow=slow,
            cap=cap,
            **kwargs,
        )
        self.fast = fast
        self.slow = slow
        self.cap = cap

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the ETF book for the current 1-minute bar."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes, self.fast, self.slow, self.cap)


if __name__ == "__main__":
    run_bot(QuantsmdIntraday1mEmaScalpBot)
