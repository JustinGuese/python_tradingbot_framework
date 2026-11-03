"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-orders-03-limit-rsi-dip
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_orders_03_limit_rsi_dip.py (Apache-2.0, (c) 2026 QuantJourney)

RSI dip buying with resting limit orders on the three core ETFs (SPY, QQQ, IWM).
When flat and RSI(14) falls below 40, a buy limit sits 1.5% under the close for 3
bars. Once filled, a sell limit sits 3% above the fill price for 3 bars, and is
re-posted while the position stays open. Each position is 15% of NAV.

Departures from source:
- Long-only and no resting orders. A CronJob keeps no state and cannot leave a
  limit order at a broker, so the limit orders are replayed statelessly over the
  last 250 daily bars with the OHLC fill model in
  ``tradingbot/utils/quantsmd_orders.py``. A new limit order is active from the
  bar after it is submitted. A fill that happens on bar j changes the position
  on bar j, and the execution price is the bar's close, not the intrabar price.
- Position size is a fixed 15% of NAV while a position is open, as in the source.
  The framework's no-trade band replaces the source's rebalancing.
"""

from typing import ClassVar

import numpy as np
import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF3, rsi, scale_to_max_gross
from tradingbot.utils.quantsmd_orders import fill_limit_buy, fill_limit_sell, ohlc_frames
from tradingbot.utils.runner import run_bot

UNIVERSE = list(ETF3)
FRACTION = 0.15
RSI_WINDOW = 14
BUY_RSI = 40.0
BUY_LIMIT_FACTOR = 0.985  # buy limit sits 1.5% under the decision close
SELL_LIMIT_FACTOR = 1.03  # sell limit sits 3% above the entry fill
EXPIRY_BARS = 3
REPLAY_BARS = 250


def _fill(order: dict, o: float, h: float, low: float) -> float | None:
    """Test one resting limit order against a bar. Returns the fill price or None."""
    if order["side"] == "buy":
        return fill_limit_buy(o, low, order["limit"])
    return fill_limit_sell(o, h, order["limit"])


def _replay(df: pd.DataFrame, buy_rsi: float, fraction: float) -> float:
    """Replay one ticker's limit-order trades over the last ``REPLAY_BARS`` bars.

    Returns the position weight after the final bar: ``fraction`` while held,
    else ``0.0``. Bars where RSI is NaN are skipped for decisions.
    """
    closes = df["close"].to_numpy(dtype=float)
    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    rsi_values = rsi(df["close"], RSI_WINDOW).to_numpy(dtype=float)

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
        if np.isnan(rsi_values[i]):
            continue
        if pos == 0.0 and order is None and rsi_values[i] < buy_rsi:
            order = {
                "side": "buy",
                "limit": round(closes[i] * BUY_LIMIT_FACTOR, 2),
                "submit": i,
                "expires": EXPIRY_BARS,
            }
        elif pos > 0.0 and order is None:
            order = {
                "side": "sell",
                "limit": round(entry * SELL_LIMIT_FACTOR, 2),
                "submit": i,
                "expires": EXPIRY_BARS,
            }
    return pos


def compute_weights(
    ohlc: dict[str, pd.DataFrame],
    buy_rsi: float = BUY_RSI,
    fraction: float = FRACTION,
) -> dict[str, float]:
    """Return ``{ticker: fraction}`` for each ticker whose limit trade is still open.

    ``ohlc`` maps tickers to open/high/low/close frames up to the current bar.
    A missing ticker or a short history contributes nothing. The result is
    scaled to a gross of at most 1.0 and is ``{}`` when nothing is held.
    """
    weights: dict[str, float] = {}
    for ticker in UNIVERSE:
        df = ohlc.get(ticker)
        if df is None or df.empty:
            continue
        weights[ticker] = _replay(df, buy_rsi, fraction)
    return scale_to_max_gross(weights, 1.0)


class QuantsmdLimitRsiDipBot(Bot):
    """
    RSI(14) dip buying with a resting buy limit 1.5% below the close.

    Args:
        buy_rsi: A buy limit is posted when RSI falls below this level while flat.
        fraction: Fixed share of NAV for each open position.

    The limit orders are replayed over the last 250 bars on every run. It never
    returns ``None``.
    """

    # The replay and RSI need more than the default 1y backtest window.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "buy_rsi": [30.0, 40.0, 50.0],
    }

    def __init__(
        self,
        buy_rsi: float = BUY_RSI,
        fraction: float = FRACTION,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdLimitRsiDipBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must exceed the RSI warmup plus the replay length.
            period="2y",
            buy_rsi=buy_rsi,
            fraction=fraction,
            **kwargs,
        )
        self.buy_rsi = buy_rsi
        self.fraction = fraction

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the limit-order book for the current bar."""
        ohlc = ohlc_frames(self.datas, self.tradeable_tickers)
        return compute_weights(ohlc, self.buy_rsi, self.fraction)


if __name__ == "__main__":
    run_bot(QuantsmdLimitRsiDipBot)
