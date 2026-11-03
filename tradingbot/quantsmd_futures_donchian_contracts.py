"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-orders-20-futures-donchian-contracts
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_orders_20_futures_donchian_contracts.py (Apache-2.0, (c) 2026 QuantJourney)

Donchian channel breakout on four futures (MES=F, MNQ=F, CL=F, GC=F), replayed
statelessly over the last 250 daily bars. A close above the highest high of the
prior 55 bars sets the direction long, a close below the lowest low of the prior
55 bars sets it short, and the direction holds until the next breakout. The bot
holds a name only while the direction is long, sized at 0.5% of NAV per ATR(20)
with a 25% cap and a 15% margin-based cap.

Paper only: yfinance continuous futures prices, with no roll and no multiplier
accounting in the book.

Departures from source:
- No resting orders: the source's breakout orders are replaced by the direction
  state, replayed on every run. The position follows the last breakout, filled at
  the bar close.
- Position size is a fixed fraction of NAV while held, not a whole-contract count.
- Long-only: the short leg is dropped, so the bot is not market-neutral and the
  short-side weight stays in cash.
- No whole-contract rounding.
"""

from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import FUTS4, atr, scale_to_max_gross
from tradingbot.utils.quantsmd_orders import ohlc_frames
from tradingbot.utils.runner import run_bot

UNIVERSE = list(FUTS4)
WINDOW = 55
REPLAY_BARS = 250
ATR_WINDOW = 20
RISK_FRACTION = 0.005
MAX_WEIGHT = 0.25
MARGIN_FRACTION = 0.15

# (multiplier, margin) per contract, as (multiplier, margin) in the source.
SPECS = {
    "MES=F": (5.0, 1500.0),
    "MNQ=F": (2.0, 2000.0),
    "CL=F": (1000.0, 6000.0),
    "GC=F": (100.0, 9000.0),
}


def _replay(
    df: pd.DataFrame,
    multiplier: float,
    margin: float,
    window: int,
) -> float:
    """Return the long weight of one futures contract on the last bar, or 0.0.

    The direction state is replayed over the last ``REPLAY_BARS`` bars, starting
    at 0. Each bar ``i >= window`` compares its close with the highest high and
    lowest low of the ``window`` bars before it, which come from the whole frame.
    A frame with ``window + 1`` rows or fewer returns 0.0.
    """
    n = len(df)
    if n < window + 1:
        return 0.0
    closes = df["close"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    atr20 = atr(df["high"], df["low"], df["close"], ATR_WINDOW).iloc[-1]
    if pd.isna(atr20) or atr20 <= 0:
        return 0.0
    direction = 0
    for i in range(max(window, n - REPLAY_BARS), n):
        prior_high = highs[i - window : i].max()
        prior_low = lows[i - window : i].min()
        if closes[i] > prior_high:
            direction = 1
        elif closes[i] < prior_low:
            direction = -1
    if direction != 1:
        return 0.0
    close = closes[-1]
    return min(
        RISK_FRACTION * close / atr20,
        MAX_WEIGHT,
        MARGIN_FRACTION * close * multiplier / margin,
    )


def compute_weights(ohlc: dict[str, pd.DataFrame], window: int = WINDOW) -> dict[str, float]:
    """Weights for the futures whose Donchian direction is long on the last bar.

    ``ohlc`` maps each ticker to its ``open, high, low, close`` frame. Tickers
    missing from ``ohlc`` or from ``SPECS`` are skipped. Each contract's weight
    comes from ``_replay``. The result goes through ``scale_to_max_gross``, so it
    sums to at most 1.0. Returns ``{}`` when nothing is long.
    """
    weights: dict[str, float] = {}
    for ticker in UNIVERSE:
        frame = ohlc.get(ticker)
        if frame is None or ticker not in SPECS:
            continue
        multiplier, margin = SPECS[ticker]
        weight = _replay(frame, multiplier, margin, window)
        if weight > 0:
            weights[ticker] = weight
    return scale_to_max_gross(weights)


class QuantsmdFuturesDonchianContractsBot(Bot):
    """
    Long-only Donchian breakout on MES, MNQ, CL and GC futures, replayed daily.

    Args:
        window: Channel length in daily bars. A breakout compares the close with
            the high or low of the ``window`` bars before it.

    The bot never returns ``None``. It returns ``{}`` when no contract is long.
    """

    # The 250-bar replay plus a 55-bar channel exceeds the default 1y window.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "window": [20, 55, 100],
    }

    def __init__(self, window: int = WINDOW, **kwargs):
        super().__init__(
            "QuantsmdFuturesDonchianContractsBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must cover the replay and the channel, or no contract is
            # evaluable and the bot holds cash without warning.
            period="2y",
            window=window,
            **kwargs,
        )
        self.window = window

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the futures book for the current bar."""
        ohlc = ohlc_frames(self.datas, self.tradeable_tickers)
        return compute_weights(ohlc, self.window)


if __name__ == "__main__":
    run_bot(QuantsmdFuturesDonchianContractsBot)
