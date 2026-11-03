"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-orders-10-trailing-stop-rsi
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_orders_10_trailing_stop_rsi.py (Apache-2.0, (c) 2026 QuantJourney)

RSI mean reversion with a trailing stop on the three core ETFs (SPY, QQQ, IWM). When
the 14-day RSI drops below 38 and the bot is flat, it buys 15% of NAV at the close.
Once the position is open, a 5% trailing sell stop is placed. Its level follows the
highest high seen since the stop was placed. When the RSI rises above 65, the stop is
cancelled and the position is sold at the close.

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
from tradingbot.utils.quantsmd_common import ETF3, rsi, scale_to_max_gross
from tradingbot.utils.quantsmd_orders import fill_stop_sell, ohlc_frames
from tradingbot.utils.runner import run_bot

UNIVERSE = list(ETF3)
RSI_WINDOW = 14
BUY_BELOW = 38.0
SELL_ABOVE = 65.0
FRACTION = 0.15
TRAIL_PCT = 0.05
REPLAY_BARS = 250


def _replay(
    df: pd.DataFrame,
    buy_below: float,
    sell_above: float,
    fraction: float,
    trail_pct: float,
) -> float:
    """Replay one ticker's trailing-stop trade over the last ``REPLAY_BARS`` bars.

    Returns the position weight after the final bar: ``fraction`` while held,
    else ``0.0``. Bars where the RSI is NaN are skipped after the stop test, so a
    pending stop can still fill on them.
    """
    rsi_values = rsi(df["close"], RSI_WINDOW).to_numpy(dtype=float)
    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)

    pos = 0.0
    stop_live = False
    high_water = 0.0
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
        if np.isnan(rsi_values[i]):
            continue
        if pos == 0.0 and rsi_values[i] < buy_below:
            pos = fraction
        elif pos > 0.0 and not stop_live:
            stop_live = True
            high_water = closes[i]
        elif pos > 0.0 and rsi_values[i] > sell_above:
            stop_live = False
            pos = 0.0
    return pos


def compute_weights(
    ohlc: dict[str, pd.DataFrame],
    buy_below: float = BUY_BELOW,
    sell_above: float = SELL_ABOVE,
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
        weights[ticker] = _replay(df, buy_below, sell_above, fraction, trail_pct)
    return scale_to_max_gross(weights, 1.0)


class QuantsmdTrailingStopRsiBot(Bot):
    """
    RSI14 dip entries with a 5% trailing sell stop and an RSI-65 exit.

    Args:
        buy_below: Enter when flat and the RSI is below this level.
        sell_above: Cancel the stop and sell when held and the RSI is above this level.
        fraction: Fixed share of NAV for each open position.
        trail_pct: Trailing stop distance below the highest high since the stop
            was placed, as a fraction.

    The position is replayed over the last 250 bars on every run. It never
    returns ``None``.
    """

    # The RSI needs far more than the default 1y backtest window to settle.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "buy_below": [30.0, 38.0, 45.0],
        "sell_above": [60.0, 65.0, 70.0],
        "trail_pct": [0.03, 0.05, 0.07],
    }

    def __init__(
        self,
        buy_below: float = BUY_BELOW,
        sell_above: float = SELL_ABOVE,
        fraction: float = FRACTION,
        trail_pct: float = TRAIL_PCT,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdTrailingStopRsiBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must exceed the RSI window plus the replay length.
            period="2y",
            buy_below=buy_below,
            sell_above=sell_above,
            fraction=fraction,
            trail_pct=trail_pct,
            **kwargs,
        )
        self.buy_below = buy_below
        self.sell_above = sell_above
        self.fraction = fraction
        self.trail_pct = trail_pct

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the trailing-stop book for the current bar."""
        ohlc = ohlc_frames(self.datas, self.tradeable_tickers)
        return compute_weights(ohlc, self.buy_below, self.sell_above, self.fraction, self.trail_pct)


if __name__ == "__main__":
    run_bot(QuantsmdTrailingStopRsiBot)
