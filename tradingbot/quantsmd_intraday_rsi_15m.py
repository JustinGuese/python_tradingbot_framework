"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-weights-07-intraday-rsi-15m
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_07_intraday_rsi_15m.py (Apache-2.0, (c) 2026 QuantJourney)

RSI mean reversion on 15-minute bars across SPY, QQQ and IWM. Each ETF's 14-bar
RSI is tracked with hysteresis: it is bought when RSI falls below 35 and held
until RSI rises above 55. Held ETFs are equal-weighted, each capped at 25%, and
the remainder stays in cash. The book is recomputed on every bar.

The source keeps this state per instrument across bars. The bot holds no state
between CronJob runs, so the RSI hysteresis is replayed over the whole fetched
history on every call.

Departures from source:
- The source's signal-change trigger is replaced by the framework's own
  no-trade band.
"""

from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import (
    ETF3,
    closes_frame,
    equal_weight,
    rsi,
    rsi_hysteresis,
)
from tradingbot.utils.runner import run_bot

UNIVERSE = list(ETF3)
RSI_PERIOD = 14
ENTRY = 35.0
EXIT = 55.0
CAP = 0.25
MIN_ROWS = 20


def compute_weights(
    closes: pd.DataFrame,
    rsi_period: int,
    entry: float,
    exit_: float,
    cap: float,
) -> dict[str, float]:
    """Equal-weight the ETFs whose RSI hold flag is set on the last bar.

    ``closes`` holds history up to and including the current bar, with one
    column per ticker. Fewer than ``MIN_ROWS`` rows returns ``{}``. Each
    ticker's RSI is run through the hysteresis state machine over the whole
    frame, and a ticker is active when that state is held at the last bar.
    Each active weight is ``min(1/len(active), cap)``, so any clipped excess
    stays in cash.
    """
    if len(closes) < MIN_ROWS:
        return {}
    active = []
    for ticker in closes.columns:
        values = rsi(closes[ticker].astype(float), rsi_period)
        if rsi_hysteresis(values, entry, exit_):
            active.append(str(ticker))
    return equal_weight(active, cap)


class QuantsmdIntradayRsi15mBot(Bot):
    """
    15-minute RSI hysteresis on SPY, QQQ and IWM, equal weight with a 25% cap.

    Args:
        rsi_period: RSI lookback in 15-minute bars.
        entry: Buy when RSI falls below this level.
        exit: Sell when RSI rises above this level.
        cap: Maximum weight for any single ETF.

    Returns the book on every bar, so it never returns ``None``.
    """

    # yfinance serves about 60 days of 15-minute bars, so that is the longest
    # backtest window available for this interval.
    BACKTEST_PERIOD: ClassVar[str | None] = "60d"

    param_grid: ClassVar[dict] = {
        "entry": [30, 35, 40],
        "exit": [50, 55, 60],
    }

    def __init__(
        self,
        rsi_period: int = RSI_PERIOD,
        entry: float = ENTRY,
        exit: float = EXIT,
        cap: float = CAP,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdIntradayRsi15mBot",
            tickers=list(UNIVERSE),
            interval="15m",
            period="5d",
            rsi_period=rsi_period,
            entry=entry,
            exit=exit,
            cap=cap,
            **kwargs,
        )
        self.rsi_period = rsi_period
        self.entry = entry
        self.exit = exit
        self.cap = cap

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the ETF book for the current bar."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes, self.rsi_period, self.entry, self.exit, self.cap)


if __name__ == "__main__":
    run_bot(QuantsmdIntradayRsi15mBot)
