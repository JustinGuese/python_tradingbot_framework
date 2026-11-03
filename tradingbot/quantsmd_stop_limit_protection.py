"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-orders-08-stop-limit-protection
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_orders_08_stop_limit_protection.py (Apache-2.0, (c) 2026 QuantJourney)

SMA trend with a protective stop-limit on the three core ETFs (SPY, QQQ, IWM).
A bullish SMA20 / SMA50 crossover buys 15% of NAV at the close. On the bar after
entry a sell stop-limit is placed: the stop 4% below the entry close, the limit
4.5% below it. It has no expiry. A bearish crossover sells at the close and
cancels the order. Once the stop is touched but the limit is not reached, the
order stays live as a sell limit and fills on the first later bar whose high
reaches the limit.

Departures from source:
- No resting orders. A CronJob keeps no state and cannot leave a stop-limit at a
  broker, so the order is replayed statelessly over the last 250 daily bars,
  with the OHLC fill model in ``tradingbot/utils/quantsmd_orders.py``. Market
  orders act at the decision bar's close.
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
STOP_PCT = 0.04
LIMIT_PCT = 0.045
REPLAY_BARS = 250


def _replay(
    df: pd.DataFrame,
    fast: int,
    slow: int,
    fraction: float,
    stop_pct: float,
    limit_pct: float,
) -> float:
    """Replay one ticker's protective stop-limit trade over the last ``REPLAY_BARS`` bars.

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
    entry = 0.0
    has_stop = False
    triggered = False
    stop_price = 0.0
    limit_price = 0.0
    prev_signal = 0
    for i in range(max(0, len(df) - REPLAY_BARS), len(df)):
        # (a) a protective stop-limit submitted on an earlier bar is tested on this bar.
        if has_stop:
            if triggered:
                filled = fill_limit_sell(opens[i], highs[i], limit_price) is not None
            else:
                fill, triggered = fill_stop_limit_sell(opens[i], highs[i], lows[i], stop_price, limit_price)
                filled = fill is not None
            if filled:
                pos = 0.0
                has_stop = False
                triggered = False
        # (b) decisions at the close, in this order.
        if np.isnan(sma_fast[i]) or np.isnan(sma_slow[i]):
            continue
        signal = 1 if sma_fast[i] > sma_slow[i] else 0
        if prev_signal == 0 and signal == 1 and pos == 0.0:
            pos = fraction
            entry = closes[i]
        elif pos > 0.0 and not has_stop:
            stop_price = round(entry * (1.0 - stop_pct), 2)
            limit_price = round(entry * (1.0 - limit_pct), 2)
            has_stop = True
            triggered = False
        elif prev_signal == 1 and signal == 0 and pos > 0.0:
            pos = 0.0
            has_stop = False
            triggered = False
        prev_signal = signal
    return pos


def compute_weights(
    ohlc: dict[str, pd.DataFrame],
    fast: int = FAST,
    slow: int = SLOW,
    fraction: float = FRACTION,
    stop_pct: float = STOP_PCT,
    limit_pct: float = LIMIT_PCT,
) -> dict[str, float]:
    """Return ``{ticker: fraction}`` for each ticker whose position is still open.

    ``ohlc`` maps tickers to open/high/low/close frames up to the current bar.
    A missing ticker or a short history contributes nothing. The result is
    scaled to a gross of at most 1.0 and is ``{}`` when nothing is held.
    """
    weights: dict[str, float] = {}
    for ticker in UNIVERSE:
        df = ohlc.get(ticker)
        if df is None or df.empty:
            continue
        weights[ticker] = _replay(df, fast, slow, fraction, stop_pct, limit_pct)
    return scale_to_max_gross(weights, 1.0)


class QuantsmdStopLimitProtectionBot(Bot):
    """
    SMA20 / SMA50 trend entries with a sell stop-limit 4% / 4.5% below entry.

    Args:
        fast: Fast simple moving average length in daily bars.
        slow: Slow simple moving average length in daily bars. A ticker is
            entered only on a crossover where the fast average rises above it.
        fraction: Fixed share of NAV for each open position.
        stop_pct: Protective stop distance below the entry close, as a fraction.
        limit_pct: Limit distance below the entry close, as a fraction. It is
            the sell limit that fills once the stop has been touched.

    The protective stop-limit is replayed over the last 250 bars on every run.
    It never returns ``None``.
    """

    # A 200-bar SMA needs more than the default 1y backtest window.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "fast": [10, 20, 30],
        "slow": [50, 100, 200],
        "stop_pct": [0.03, 0.04, 0.06],
    }

    def __init__(
        self,
        fast: int = FAST,
        slow: int = SLOW,
        fraction: float = FRACTION,
        stop_pct: float = STOP_PCT,
        limit_pct: float = LIMIT_PCT,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdStopLimitProtectionBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must exceed the slow window plus the replay length.
            period="2y",
            fast=fast,
            slow=slow,
            fraction=fraction,
            stop_pct=stop_pct,
            limit_pct=limit_pct,
            **kwargs,
        )
        self.fast = fast
        self.slow = slow
        self.fraction = fraction
        self.stop_pct = stop_pct
        self.limit_pct = limit_pct

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the protected stop-limit book for the current bar."""
        ohlc = ohlc_frames(self.datas, self.tradeable_tickers)
        return compute_weights(ohlc, self.fast, self.slow, self.fraction, self.stop_pct, self.limit_pct)


if __name__ == "__main__":
    run_bot(QuantsmdStopLimitProtectionBot)
