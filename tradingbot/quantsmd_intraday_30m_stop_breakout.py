"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-orders-16-intraday-30m-stop-breakout
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_orders_16_intraday_30m_stop_breakout.py (Apache-2.0, (c) 2026 QuantJourney)

Intraday 30-minute stop breakout on SPY, QQQ and IWM. While flat, a buy stop sits
0.1% above the highest high of the 12 bars before the current bar. It stays active
for 3 bars and fills when a bar's high reaches it, at the greater of that bar's open
and the stop. A position is closed at the close once it has been held for 8 bars,
counted from the bar on which the stop order was submitted. Each name holds a fixed
20% of NAV while the position is open.

Departures from source:
- No resting orders: the breakout stop is replayed statelessly over all bars in the
  live window on every run, with the OHLC fill model in ``quantsmd_orders``. Market
  orders act at the decision bar's close. A stop that fills on bar j changes the
  position on bar j, at ``max(open, stop)``. The framework records the bar's close
  as the execution price, not the intrabar fill price.
- Only one pending stop per name at a time. The source stacks a new stop on every
  flat bar.
"""

from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF3, scale_to_max_gross
from tradingbot.utils.quantsmd_orders import fill_stop_buy, ohlc_frames
from tradingbot.utils.runner import run_bot

UNIVERSE = list(ETF3)
FRACTION = 0.20
LOOKBACK = 12
HOLD_BARS = 8
STOP_BUFFER = 0.001
EXPIRES_AFTER_BARS = 3


def _replay(df: pd.DataFrame) -> float:
    """Return the held weight of one name after replaying every bar in ``df``.

    Decisions start at bar ``LOOKBACK``. While flat with no stop live, the buy stop
    sits at ``round(max(high[i-LOOKBACK:i]) * (1 + STOP_BUFFER), 2)`` and its entry
    bar is set at submission. A filled stop sets the position. Once held for
    ``HOLD_BARS`` bars from the entry bar, the position is sold at the close.
    """
    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    n = len(df)

    pos = 0.0
    entry_bar = -1
    pending: dict | None = None
    for i in range(LOOKBACK, n):
        # (a) a buy stop submitted on an earlier bar is tested on this bar.
        if pending is not None and i > pending["submit"]:
            fill = fill_stop_buy(opens[i], highs[i], pending["stop"])
            if fill is not None:
                pos = FRACTION
                pending = None
            elif i >= pending["submit"] + EXPIRES_AFTER_BARS:
                pending = None
        # (b) decisions at the close.
        if pos > 0.0 and i - entry_bar >= HOLD_BARS:
            pos = 0.0
            pending = None
            entry_bar = -1
            continue
        if pos == 0.0 and pending is None:
            prior_high = highs[i - LOOKBACK : i].max()
            pending = {"submit": i, "stop": round(prior_high * (1.0 + STOP_BUFFER), 2)}
            entry_bar = i
    return pos


def compute_weights(ohlc: dict[str, pd.DataFrame]) -> dict[str, float]:
    """Return ``{ticker: FRACTION}`` for each ticker whose breakout position is held.

    ``ohlc`` maps tickers to open/high/low/close frames up to the current bar. A
    missing ticker or a short history contributes nothing. The result is scaled to a
    gross of at most 1.0 and is ``{}`` when nothing is held.
    """
    weights: dict[str, float] = {}
    for ticker in UNIVERSE:
        df = ohlc.get(ticker)
        if df is None or df.empty:
            continue
        pos = _replay(df)
        if pos > 0:
            weights[ticker] = pos
    return scale_to_max_gross(weights, 1.0)


class QuantsmdIntraday30mStopBreakoutBot(Bot):
    """
    30-minute 12-bar high stop breakout on SPY, QQQ and IWM, 8-bar time stop.

    Each open position holds 20% of NAV. The stop order is replayed over the live
    window on every run. The bot never returns ``None``.
    """

    # yfinance serves about 60 days of 30m bars.
    BACKTEST_PERIOD: ClassVar[str | None] = "60d"

    def __init__(self, **kwargs):
        super().__init__(
            "QuantsmdIntraday30mStopBreakoutBot",
            tickers=list(UNIVERSE),
            interval="30m",
            period="5d",
            **kwargs,
        )

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the breakout book for the current bar."""
        ohlc = ohlc_frames(self.datas, self.tradeable_tickers)
        return compute_weights(ohlc)


if __name__ == "__main__":
    run_bot(QuantsmdIntraday30mStopBreakoutBot)
