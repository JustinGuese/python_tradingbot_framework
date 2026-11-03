"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-orders-12-bracket-trend
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_orders_12_bracket_trend.py (Apache-2.0, (c) 2026 QuantJourney)

SMA trend with a bracket on the three core ETFs (SPY, QQQ, IWM). A bullish
SMA20 / SMA50 crossover buys 15% of NAV at the close, with a take-profit 6%
above and a stop-loss 3% below that close. A bearish crossover sells at the
close and drops the bracket. While held, the bracket exits on the first bar
whose range reaches either level, stop first when one bar touches both.

Departures from source:
- No resting orders. A CronJob keeps no state and cannot leave a bracket at a
  broker, so the bracket is replayed statelessly over the last 250 daily bars,
  with the OHLC fill model in ``tradingbot/utils/quantsmd_orders.py``. Market
  orders act at the decision bar's close. A bracket exit fills at its level, or
  at the bar's open when the open gaps past it.
- Position size is a fixed 15% of NAV per open position, as in the source. The
  framework's no-trade band replaces the source's rebalancing.
"""

from typing import ClassVar

import numpy as np
import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF3, scale_to_max_gross
from tradingbot.utils.quantsmd_orders import bracket_exit, ohlc_frames
from tradingbot.utils.runner import run_bot

UNIVERSE = list(ETF3)
FAST = 20
SLOW = 50
FRACTION = 0.15
TAKE_PROFIT = 0.06
STOP_LOSS = 0.03
REPLAY_BARS = 250


def _replay(
    df: pd.DataFrame,
    fast: int,
    slow: int,
    fraction: float,
    take_profit: float,
    stop_loss: float,
) -> float:
    """Replay one ticker's bracket trade over the last ``REPLAY_BARS`` bars.

    Returns the position weight after the final bar: ``fraction`` while held,
    else ``0.0``. Bars where either average is NaN are skipped.
    """
    close = df["close"]
    sma_fast = close.rolling(fast, min_periods=fast).mean().to_numpy(dtype=float)
    sma_slow = close.rolling(slow, min_periods=slow).mean().to_numpy(dtype=float)
    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = close.to_numpy(dtype=float)

    pos = 0.0
    take = 0.0
    stop = 0.0
    entry_bar = -1
    prev_signal = 0
    for i in range(max(0, len(df) - REPLAY_BARS), len(df)):
        # (a) a bracket is tested from the bar after its entry.
        if pos > 0.0 and i > entry_bar and bracket_exit(opens[i], highs[i], lows[i], take, stop) is not None:
            pos = 0.0
        # (b) decisions at the close.
        if np.isnan(sma_fast[i]) or np.isnan(sma_slow[i]):
            continue
        signal = 1 if sma_fast[i] > sma_slow[i] else 0
        if prev_signal == 0 and signal == 1 and pos == 0.0:
            pos = fraction
            entry_bar = i
            take = round(closes[i] * (1.0 + take_profit), 2)
            stop = round(closes[i] * (1.0 - stop_loss), 2)
        elif prev_signal == 1 and signal == 0 and pos > 0.0:
            pos = 0.0
        prev_signal = signal
    return pos


def compute_weights(
    ohlc: dict[str, pd.DataFrame],
    fast: int = FAST,
    slow: int = SLOW,
    fraction: float = FRACTION,
    take_profit: float = TAKE_PROFIT,
    stop_loss: float = STOP_LOSS,
) -> dict[str, float]:
    """Return ``{ticker: fraction}`` for each ticker whose bracket trade is still open.

    ``ohlc`` maps tickers to open/high/low/close frames up to the current bar.
    A missing ticker or a short history contributes nothing. The result is
    scaled to a gross of at most 1.0 and is ``{}`` when nothing is held.
    """
    weights: dict[str, float] = {}
    for ticker in UNIVERSE:
        df = ohlc.get(ticker)
        if df is None or df.empty:
            continue
        weights[ticker] = _replay(df, fast, slow, fraction, take_profit, stop_loss)
    return scale_to_max_gross(weights, 1.0)


class QuantsmdBracketTrendBot(Bot):
    """
    SMA20 / SMA50 trend entries with a 6% take-profit and 3% stop-loss bracket.

    Args:
        fast: Fast simple moving average length in daily bars.
        slow: Slow simple moving average length in daily bars. A ticker is
            entered only on a crossover where the fast average rises above it.
        fraction: Fixed share of NAV for each open position.
        take_profit: Target distance above the entry close, as a fraction.
        stop_loss: Stop distance below the entry close, as a fraction.

    The bracket is replayed over the last 250 bars on every run. It never
    returns ``None``.
    """

    # A 200-bar SMA needs more than the default 1y backtest window.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "fast": [10, 20, 30],
        "slow": [50, 100, 200],
    }

    def __init__(
        self,
        fast: int = FAST,
        slow: int = SLOW,
        fraction: float = FRACTION,
        take_profit: float = TAKE_PROFIT,
        stop_loss: float = STOP_LOSS,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdBracketTrendBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must exceed the slow window plus the replay length.
            period="2y",
            fast=fast,
            slow=slow,
            fraction=fraction,
            take_profit=take_profit,
            stop_loss=stop_loss,
            **kwargs,
        )
        self.fast = fast
        self.slow = slow
        self.fraction = fraction
        self.take_profit = take_profit
        self.stop_loss = stop_loss

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the bracket book for the current bar."""
        ohlc = ohlc_frames(self.datas, self.tradeable_tickers)
        return compute_weights(ohlc, self.fast, self.slow, self.fraction, self.take_profit, self.stop_loss)


if __name__ == "__main__":
    run_bot(QuantsmdBracketTrendBot)
