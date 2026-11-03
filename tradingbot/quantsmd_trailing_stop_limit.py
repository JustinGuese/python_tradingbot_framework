"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-orders-11-trailing-stop-limit
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_orders_11_trailing_stop_limit.py (Apache-2.0, (c) 2026 QuantJourney)

SMA trend with a trailing stop-limit on the three core ETFs (SPY, QQQ, IWM). A bullish
SMA20 / SMA50 crossover buys 15% of NAV at the close. Once the position is open, a
trailing sell stop-limit is placed. Its stop sits a fixed dollar trail below the highest
high seen since it was placed, and its limit sits a fixed dollar offset below the stop.
A bearish crossover sells at the close and cancels the stop-limit.

Departures from source:
- Long-only and no resting orders. A CronJob keeps no state and cannot leave a
  trailing stop-limit at a broker, so the position is replayed statelessly over the
  last 250 daily bars with the OHLC fill model in ``tradingbot/utils/quantsmd_orders.py``.
  Market orders act at the decision bar's close. The stop-limit is tested from the bar
  after it is placed, against the high water from before that bar. A stop that triggers
  and fills at its limit changes the position on that bar. A triggered stop whose limit
  does not fill stays live and fills later at the limit, as a plain sell limit.
- Position size is a fixed 15% of NAV per open position, as in the source. The
  framework's no-trade band replaces the source's rebalancing.
"""

from typing import ClassVar

import numpy as np
import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF3, scale_to_max_gross
from tradingbot.utils.quantsmd_orders import fill_limit_sell, fill_stop_limit_sell, ohlc_frames
from tradingbot.utils.runner import run_bot

UNIVERSE = list(ETF3)
FAST = 20
SLOW = 50
FRACTION = 0.15
TRAIL_PCT = 0.04  # trail amount, as a fraction of the close at submission
LIMIT_PCT = 0.005  # limit offset below the stop, as a fraction of the close at submission
REPLAY_BARS = 250


def _replay(
    df: pd.DataFrame,
    fast: int,
    slow: int,
    fraction: float,
    trail_pct: float,
    limit_pct: float,
) -> float:
    """Replay one ticker's trailing stop-limit trade over the last ``REPLAY_BARS`` bars.

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
    triggered = False  # the stop has fired; the order is now a live sell limit
    limit = 0.0
    high_water = 0.0
    trail_amount = 0.0
    limit_offset = 0.0
    prev_signal = 0
    for i in range(max(0, len(df) - REPLAY_BARS), len(df)):
        # (a) the trailing stop-limit placed on an earlier bar. Before it triggers
        # the level comes from the high water before this bar; after, it is a limit.
        if stop_live:
            if triggered:
                if fill_limit_sell(opens[i], highs[i], limit) is not None:
                    pos = 0.0
                    stop_live = False
                    triggered = False
            else:
                level = high_water - trail_amount
                fill, fired = fill_stop_limit_sell(opens[i], highs[i], lows[i], level, level - limit_offset)
                if fill is not None:
                    pos = 0.0
                    stop_live = False
                elif fired:
                    triggered = True
                    limit = level - limit_offset
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
            triggered = False
            high_water = closes[i]
            trail_amount = round(closes[i] * trail_pct, 2)
            limit_offset = round(closes[i] * limit_pct, 2)
        elif prev_signal == 1 and signal == 0 and pos > 0.0:
            stop_live = False
            triggered = False
            pos = 0.0
        prev_signal = signal
    return pos


def compute_weights(
    ohlc: dict[str, pd.DataFrame],
    fast: int = FAST,
    slow: int = SLOW,
    fraction: float = FRACTION,
    trail_pct: float = TRAIL_PCT,
    limit_pct: float = LIMIT_PCT,
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
        weights[ticker] = _replay(df, fast, slow, fraction, trail_pct, limit_pct)
    return scale_to_max_gross(weights, 1.0)


class QuantsmdTrailingStopLimitBot(Bot):
    """
    SMA20 / SMA50 trend entries with a trailing sell stop-limit.

    Args:
        fast: Fast simple moving average length in daily bars.
        slow: Slow simple moving average length in daily bars. A ticker is
            entered only on a crossover where the fast average rises above it.
        fraction: Fixed share of NAV for each open position.
        trail_pct: Trail below the close at submission, as a fraction. The stop
            then trails the highest high seen since it was placed.
        limit_pct: Limit offset below the stop, as a fraction of the close at
            submission.

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
        limit_pct: float = LIMIT_PCT,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdTrailingStopLimitBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must exceed the slow window plus the replay length.
            period="2y",
            fast=fast,
            slow=slow,
            fraction=fraction,
            trail_pct=trail_pct,
            limit_pct=limit_pct,
            **kwargs,
        )
        self.fast = fast
        self.slow = slow
        self.fraction = fraction
        self.trail_pct = trail_pct
        self.limit_pct = limit_pct

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the trailing stop-limit book for the current bar."""
        ohlc = ohlc_frames(self.datas, self.tradeable_tickers)
        return compute_weights(ohlc, self.fast, self.slow, self.fraction, self.trail_pct, self.limit_pct)


if __name__ == "__main__":
    run_bot(QuantsmdTrailingStopLimitBot)
