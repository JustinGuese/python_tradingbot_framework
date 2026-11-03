"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-orders-13-bracket-rsi-reversion
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_orders_13_bracket_rsi_reversion.py (Apache-2.0, (c) 2026 QuantJourney)

RSI mean reversion with a bracket on the three core ETFs (SPY, QQQ, IWM). A flat
ticker whose 14-bar RSI falls below 35 is bought at the close, with a
take-profit 4% above and a stop-loss 2% below that close. A held ticker whose
RSI rises above 70 is sold at the close and the bracket is dropped. While held,
the bracket exits on the first bar whose range reaches either level, stop first
when one bar touches both.

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
from tradingbot.utils.quantsmd_common import ETF3, rsi, scale_to_max_gross
from tradingbot.utils.quantsmd_orders import bracket_exit, ohlc_frames
from tradingbot.utils.runner import run_bot

UNIVERSE = list(ETF3)
RSI_PERIOD = 14
BUY_BELOW = 35.0
SELL_ABOVE = 70.0
FRACTION = 0.15
TAKE_PROFIT = 0.04
STOP_LOSS = 0.02
REPLAY_BARS = 250


def _replay(
    df: pd.DataFrame,
    buy_below: float,
    sell_above: float,
    fraction: float,
    take_profit: float,
    stop_loss: float,
) -> float:
    """Replay one ticker's RSI bracket trade over the last ``REPLAY_BARS`` bars.

    Returns the position weight after the final bar: ``fraction`` while held,
    else ``0.0``. Bars where RSI is NaN are skipped.
    """
    closes = df["close"].to_numpy(dtype=float)
    rsi_values = rsi(df["close"], RSI_PERIOD).to_numpy(dtype=float)
    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)

    pos = 0.0
    take = 0.0
    stop = 0.0
    entry_bar = -1
    for i in range(max(0, len(df) - REPLAY_BARS), len(df)):
        # (a) a bracket is tested from the bar after its entry.
        if pos > 0.0 and i > entry_bar and bracket_exit(opens[i], highs[i], lows[i], take, stop) is not None:
            pos = 0.0
        # (b) decisions at the close.
        if np.isnan(rsi_values[i]):
            continue
        if pos == 0.0 and rsi_values[i] < buy_below:
            pos = fraction
            entry_bar = i
            take = round(closes[i] * (1.0 + take_profit), 2)
            stop = round(closes[i] * (1.0 - stop_loss), 2)
        elif pos > 0.0 and rsi_values[i] > sell_above:
            pos = 0.0
    return pos


def compute_weights(
    ohlc: dict[str, pd.DataFrame],
    buy_below: float = BUY_BELOW,
    sell_above: float = SELL_ABOVE,
    fraction: float = FRACTION,
    take_profit: float = TAKE_PROFIT,
    stop_loss: float = STOP_LOSS,
) -> dict[str, float]:
    """Return ``{ticker: fraction}`` for each ticker whose RSI trade is still open.

    ``ohlc`` maps tickers to open/high/low/close frames up to the current bar.
    A missing ticker or a short history contributes nothing. The result is
    scaled to a gross of at most 1.0 and is ``{}`` when nothing is held.
    """
    weights: dict[str, float] = {}
    for ticker in UNIVERSE:
        df = ohlc.get(ticker)
        if df is None or df.empty:
            continue
        weights[ticker] = _replay(df, buy_below, sell_above, fraction, take_profit, stop_loss)
    return scale_to_max_gross(weights, 1.0)


class QuantsmdBracketRsiReversionBot(Bot):
    """
    14-bar RSI dip buys on the three core ETFs, with a 4% take-profit and 2% stop-loss.

    Args:
        buy_below: Buy a flat ticker when its RSI is below this level.
        sell_above: Sell a held ticker when its RSI is above this level.
        fraction: Fixed share of NAV for each open position.
        take_profit: Target distance above the entry close, as a fraction.
        stop_loss: Stop distance below the entry close, as a fraction.

    The bracket is replayed over the last 250 bars on every run. It never
    returns ``None``.
    """

    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "buy_below": [30, 35, 40],
        "sell_above": [65, 70, 75],
    }

    def __init__(
        self,
        buy_below: float = BUY_BELOW,
        sell_above: float = SELL_ABOVE,
        fraction: float = FRACTION,
        take_profit: float = TAKE_PROFIT,
        stop_loss: float = STOP_LOSS,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdBracketRsiReversionBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must cover the RSI warm-up plus the replay length.
            period="2y",
            buy_below=buy_below,
            sell_above=sell_above,
            fraction=fraction,
            take_profit=take_profit,
            stop_loss=stop_loss,
            **kwargs,
        )
        self.buy_below = buy_below
        self.sell_above = sell_above
        self.fraction = fraction
        self.take_profit = take_profit
        self.stop_loss = stop_loss

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the bracket book for the current bar."""
        ohlc = ohlc_frames(self.datas, self.tradeable_tickers)
        return compute_weights(
            ohlc,
            self.buy_below,
            self.sell_above,
            self.fraction,
            self.take_profit,
            self.stop_loss,
        )


if __name__ == "__main__":
    run_bot(QuantsmdBracketRsiReversionBot)
