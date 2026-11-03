"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-orders-04-limit-trend-pullback
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_orders_04_limit_trend_pullback.py (Apache-2.0, (c) 2026 QuantJourney)

Trend pullback with resting limit orders on the three core ETFs (SPY, QQQ, IWM).
While flat and the close is above its 50-day simple moving average, a buy limit
sits 1% under the close for 2 bars. Once filled, a sell limit sits 4% above the
fill price for 5 bars and is re-posted while the position stays open. If the
close falls below the average while held, the bot cancels its order and sells at
the close. Each position is 12% of NAV.

Departures from source:
- Long-only and no resting orders. A CronJob keeps no state and cannot leave a
  limit order at a broker, so the limit orders are replayed statelessly over the
  last 250 daily bars with the OHLC fill model in
  ``tradingbot/utils/quantsmd_orders.py``. Market orders act at the decision
  bar's close. A new limit order is active from the bar after it is submitted.
  A fill that happens on bar j changes the position on bar j, and the execution
  price is the bar's close, not the intrabar price.
- Position size is a fixed 12% of NAV while a position is open, as in the
  source. The framework's no-trade band replaces the source's rebalancing.
"""

from typing import ClassVar

import numpy as np
import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF3, scale_to_max_gross
from tradingbot.utils.quantsmd_orders import fill_limit_buy, fill_limit_sell, ohlc_frames
from tradingbot.utils.runner import run_bot

UNIVERSE = list(ETF3)
SMA_WINDOW = 50
FRACTION = 0.12
BUY_LIMIT_FACTOR = 0.99  # buy limit sits 1% under the decision close
SELL_LIMIT_FACTOR = 1.04  # sell limit sits 4% above the entry fill
BUY_EXPIRY_BARS = 2
SELL_EXPIRY_BARS = 5
REPLAY_BARS = 250


def _fill(order: dict, o: float, h: float, low: float) -> float | None:
    """Test one resting limit order against a bar. Returns the fill price or None."""
    if order["side"] == "buy":
        return fill_limit_buy(o, low, order["limit"])
    return fill_limit_sell(o, h, order["limit"])


def _replay(df: pd.DataFrame, sma_window: int, fraction: float) -> float:
    """Replay one ticker's pullback trades over the last ``REPLAY_BARS`` bars.

    Returns the position weight after the final bar: ``fraction`` while held,
    else ``0.0``. Bars where the moving average is NaN are skipped.
    """
    closes = df["close"].to_numpy(dtype=float)
    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    sma = df["close"].rolling(sma_window, min_periods=sma_window).mean().to_numpy(dtype=float)

    pos = 0.0
    entry = 0.0
    order: dict | None = None  # at most one live order per ticker
    for i in range(max(0, len(df) - REPLAY_BARS), len(df)):
        # (a) an order is tested from the bar after its submission.
        if order is not None and i > order["submit"]:
            price = _fill(order, opens[i], highs[i], lows[i])
            if price is not None:
                if order["side"] == "buy":
                    pos = fraction
                    entry = price
                else:
                    pos = 0.0
                order = None
            elif i - order["submit"] >= order["expires"]:
                order = None
        # (b) decisions at the close.
        if np.isnan(sma[i]):
            continue
        if pos == 0.0 and order is None and closes[i] > sma[i]:
            order = {
                "side": "buy",
                "limit": round(closes[i] * BUY_LIMIT_FACTOR, 2),
                "submit": i,
                "expires": BUY_EXPIRY_BARS,
            }
        elif pos > 0.0 and closes[i] < sma[i]:
            # Market sell at the close; cancels any live order.
            pos = 0.0
            order = None
        elif pos > 0.0 and order is None:
            order = {
                "side": "sell",
                "limit": round(entry * SELL_LIMIT_FACTOR, 2),
                "submit": i,
                "expires": SELL_EXPIRY_BARS,
            }
    return pos


def compute_weights(
    ohlc: dict[str, pd.DataFrame],
    sma_window: int = SMA_WINDOW,
    fraction: float = FRACTION,
) -> dict[str, float]:
    """Return ``{ticker: fraction}`` for each ticker whose pullback trade is still open.

    ``ohlc`` maps tickers to open/high/low/close frames up to the current bar.
    A missing ticker or a short history contributes nothing. The result is
    scaled to a gross of at most 1.0 and is ``{}`` when nothing is held.
    """
    weights: dict[str, float] = {}
    for ticker in UNIVERSE:
        df = ohlc.get(ticker)
        if df is None or df.empty:
            continue
        weights[ticker] = _replay(df, sma_window, fraction)
    return scale_to_max_gross(weights, 1.0)


class QuantsmdLimitTrendPullbackBot(Bot):
    """
    SMA50 trend filter with a buy limit 1% under the close and a 4% sell limit.

    Args:
        sma_window: Simple moving average length in daily bars. A buy limit is
            posted only while the close is above this average.
        fraction: Fixed share of NAV for each open position.

    The limit orders are replayed over the last 250 bars on every run. It never
    returns ``None``.
    """

    # A 50-bar SMA and the replay need more than the default 1y backtest window.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "sma_window": [20, 50, 100],
    }

    def __init__(
        self,
        sma_window: int = SMA_WINDOW,
        fraction: float = FRACTION,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdLimitTrendPullbackBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must exceed the SMA window plus the replay length.
            period="2y",
            sma_window=sma_window,
            fraction=fraction,
            **kwargs,
        )
        self.sma_window = sma_window
        self.fraction = fraction

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the limit-order book for the current bar."""
        ohlc = ohlc_frames(self.datas, self.tradeable_tickers)
        return compute_weights(ohlc, self.sma_window, self.fraction)


if __name__ == "__main__":
    run_bot(QuantsmdLimitTrendPullbackBot)
