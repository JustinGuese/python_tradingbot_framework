"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-orders-15-intraday-5m-bracket-reversion
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_orders_15_intraday_5m_bracket_reversion.py (Apache-2.0, (c) 2026 QuantJourney)

Intraday 5-minute RSI reversion on SPY, QQQ and IWM with a bracket on every entry.
When flat and RSI(14) falls below 30, the bot buys at the bar close with a take
profit 0.6% above and a stop loss 0.4% below. From the next bar the bracket is
checked on each bar's open, high and low, and the stop wins a bar that touches both
levels. Each name holds a fixed 20% of NAV while the bracket is live.

Departures from source:
- No resting orders: the bracket is replayed statelessly over all bars in the live
  window on every run. Entries fill at the bar close and exits fill at the bracket
  level, both as in the fill model in ``quantsmd_orders``.
- Position size is a fixed fraction of NAV while held (20%), not a share count.
- No end-of-day flatten; neither the source nor this bot closes positions at the
  session close.
"""

from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF3, rsi, scale_to_max_gross
from tradingbot.utils.quantsmd_orders import bracket_exit, ohlc_frames
from tradingbot.utils.runner import run_bot

UNIVERSE = list(ETF3)
RSI_WINDOW = 14
RSI_ENTRY = 30.0
TAKE_PROFIT = 0.006
STOP_LOSS = 0.004
FRACTION = 0.20


def _replay(df: pd.DataFrame) -> float:
    """Return the held weight of one name after replaying every bar in ``df``.

    RSI is computed over the whole frame before the replay. While flat, a bar with
    RSI below ``RSI_ENTRY`` buys at that bar's close and sets the bracket levels
    from the close. From the bar after entry, each bar's ``bracket_exit`` closes
    the position on a stop or target hit. The bracket is not checked on the bar
    that opened it, and a bar that closes a position does not also open one.
    """
    rsi_values = rsi(df["close"], RSI_WINDOW).to_numpy(dtype=float)
    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    pos = 0.0
    take_profit = 0.0
    stop_loss = 0.0
    for i in range(len(df)):
        if pos > 0:
            if bracket_exit(opens[i], highs[i], lows[i], take_profit, stop_loss) is not None:
                pos = 0.0
        elif not pd.isna(rsi_values[i]) and rsi_values[i] < RSI_ENTRY:
            close = closes[i]
            pos = FRACTION
            take_profit = round(close * (1 + TAKE_PROFIT), 2)
            stop_loss = round(close * (1 - STOP_LOSS), 2)
    return pos


def compute_weights(ohlc: dict[str, pd.DataFrame]) -> dict[str, float]:
    """Weights for every name whose bracket is live after the replay.

    ``ohlc`` maps each ticker to its ``open, high, low, close`` frame. Tickers
    missing from ``ohlc`` are skipped. Each live name holds ``FRACTION``, and the
    result goes through ``scale_to_max_gross``. Returns ``{}`` when nothing is held.
    """
    weights: dict[str, float] = {}
    for ticker in UNIVERSE:
        frame = ohlc.get(ticker)
        if frame is None:
            continue
        pos = _replay(frame)
        if pos > 0:
            weights[ticker] = pos
    return scale_to_max_gross(weights)


class QuantsmdIntraday5mBracketReversionBot(Bot):
    """
    5-minute RSI(14) reversion on SPY, QQQ and IWM with a 0.6% / 0.4% bracket.

    Each entry holds 20% of NAV. The bracket is replayed over the live window on
    every run. The bot never returns ``None``.
    """

    # yfinance serves about 60 days of 5m bars.
    BACKTEST_PERIOD: ClassVar[str | None] = "60d"

    def __init__(self, **kwargs):
        super().__init__(
            "QuantsmdIntraday5mBracketReversionBot",
            tickers=list(UNIVERSE),
            interval="5m",
            period="5d",
            **kwargs,
        )

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the bracket book for the current bar."""
        ohlc = ohlc_frames(self.datas, self.tradeable_tickers)
        return compute_weights(ohlc)


if __name__ == "__main__":
    run_bot(QuantsmdIntraday5mBracketReversionBot)
