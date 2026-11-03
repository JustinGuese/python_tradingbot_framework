"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-orders-09-trailing-stop-trend
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_orders_09_trailing_stop_trend.py (Apache-2.0, (c) 2026 QuantJourney)

SMA trend with a trailing stop on the three core ETFs (SPY, QQQ, IWM). A bullish
SMA20 / SMA50 crossover buys 15% of NAV at the close. Once the position is open, a
4% trailing sell stop is placed. Its level follows the highest high seen since the
stop was placed. A bearish crossover sells at the close and cancels the stop.

Departures from source:
- No resting orders. A CronJob keeps no state and cannot leave a trailing stop at a
  broker, so the position is replayed statelessly over the last 250 daily bars, with
  the OHLC fill model in ``tradingbot/utils/quantsmd_orders.py``. Market orders act at
  the decision bar's close. The trailing stop is tested from the bar after it is
  placed, against the high water from before that bar. A stop fills at its level, or
  at the bar's open when the open gaps past it.
- Position size is a fixed 15% of NAV per open position, as in the source. The
  framework's no-trade band replaces the source's rebalancing.
"""

from typing import ClassVar

import numpy as np
import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF3, scale_to_max_gross
from tradingbot.utils.quantsmd_orders import fill_stop_sell, ohlc_frames
from tradingbot.utils.runner import run_bot

UNIVERSE = list(ETF3)
FAST = 20
SLOW = 50
FRACTION = 0.15
TRAIL_PCT = 0.04
REPLAY_BARS = 250


def _replay(
    df: pd.DataFrame,
    fast: int,
    slow: int,
    fraction: float,
    trail_pct: float,
) -> float:
    """Replay one ticker's trailing-stop trade over the last ``REPLAY_BARS`` bars.

    Returns the position weight after the final bar: ``fraction`` while held,
    else ``0.0``. Bars where either average is NaN are skipped after the stop
    test, so a pending stop can still fill on them.
    """
    close = df["close"]
    sma_fast = close.rolling(fast, min_periods=fast).mean().to_numpy(dtype=float)
    sma_slow = close.rolling(slow, min_periods=slow).mean().to_numpy(dtype=float)
    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = close.to_numpy(dtype=float)

    pos = 0.0
    stop_live = False
    high_water = 0.0
    prev_signal = 0
    for i in range(max(0, len(df) - REPLAY_BARS), len(df)):
        # (a) a trailing stop placed on an earlier bar is tested against the high
        # water from before this bar, then the high water is raised.
        if stop_live:
            if fill_stop_sell(opens[i], lows[i], high_water * (1.0 - trail_pct)) is not None:
                pos = 0.0
                stop_live = False
            else:
                high_water = max(high_water, highs[i])
        # (b) decisions at the close.
        if np.isnan(sma_fast[i]) or np.isnan(sma_slow[i]):
            continue
        signal = 1 if sma_fast[i] > sma_slow[i] else 0
        if prev_signal == 0 and signal == 1 and pos == 0.0:
            pos = fraction
        elif pos > 0.0 and not stop_live:
            stop_live = True
            high_water = closes[i]
        elif prev_signal == 1 and signal == 0 and pos > 0.0:
            stop_live = False
            pos = 0.0
        prev_signal = signal
    return pos


def compute_weights(
    ohlc: dict[str, pd.DataFrame],
    fast: int = FAST,
    slow: int = SLOW,
    fraction: float = FRACTION,
    trail_pct: float = TRAIL_PCT,
) -> dict[str, float]:
    """Return ``{ticker: fraction}`` for each ticker whose trade is still open.

    ``ohlc`` maps tickers to open/high/low/close frames up to the current bar.
    A missing ticker or a short history contributes nothing. The result is
    scaled to a gross of at most 1.0 and is ``{}`` when nothing is held.
    """
    weights: dict[str, float] = {}
    for ticker in UNIVERSE:
        df = ohlc.get(ticker)
        if df is None or df.empty:
            continue
        weights[ticker] = _replay(df, fast, slow, fraction, trail_pct)
    return scale_to_max_gross(weights, 1.0)


class QuantsmdTrailingStopTrendBot(Bot):
    """
    SMA20 / SMA50 trend entries with a 4% trailing sell stop.

    Args:
        fast: Fast simple moving average length in daily bars.
        slow: Slow simple moving average length in daily bars. A ticker is
            entered only on a crossover where the fast average rises above it.
        fraction: Fixed share of NAV for each open position.
        trail_pct: Trailing stop distance below the highest high since the stop
            was placed, as a fraction.

    The position is replayed over the last 250 bars on every run. It never
    returns ``None``.
    """

    # A 200-bar SMA needs more than the default 1y backtest window.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "fast": [10, 20, 30],
        "slow": [50, 100, 200],
        "trail_pct": [0.03, 0.04, 0.05],
    }

    def __init__(
        self,
        fast: int = FAST,
        slow: int = SLOW,
        fraction: float = FRACTION,
        trail_pct: float = TRAIL_PCT,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdTrailingStopTrendBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must exceed the slow window plus the replay length.
            period="2y",
            fast=fast,
            slow=slow,
            fraction=fraction,
            trail_pct=trail_pct,
            **kwargs,
        )
        self.fast = fast
        self.slow = slow
        self.fraction = fraction
        self.trail_pct = trail_pct

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the trailing-stop book for the current bar."""
        ohlc = ohlc_frames(self.datas, self.tradeable_tickers)
        return compute_weights(ohlc, self.fast, self.slow, self.fraction, self.trail_pct)


if __name__ == "__main__":
    run_bot(QuantsmdTrailingStopTrendBot)
