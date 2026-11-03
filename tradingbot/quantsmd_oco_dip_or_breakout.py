"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-orders-14-oco-dip-or-breakout
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_orders_14_oco_dip_or_breakout.py (Apache-2.0, (c) 2026 QuantJourney)

One-cancels-the-other entry on the three core ETFs (SPY, QQQ, IWM). While flat, an OCO
pair is placed: a buy limit 2% under the close (a dip entry) and a buy stop 2% over the
close (a breakout entry). The first leg to fill cancels the other. A position is closed
at the market after 10 bars. Each position is 10% of NAV.

Departures from source:
- Long-only and no resting orders. A CronJob keeps no state and cannot leave an OCO pair
  at a broker, so the orders are replayed statelessly over the last 250 daily bars with
  the OHLC fill model in ``tradingbot/utils/quantsmd_orders.py``. A pair is placed only on
  a Monday (the source places one every fifth bar, which depends on the window start).
  Market orders act at the decision bar's close. A fill on bar j changes the position on
  bar j, and the execution price is the bar's close, not the intrabar fill price.
- Position size is a fixed 10% of NAV while a position is open, as in the source. The
  framework's no-trade band replaces the source's rebalancing.
"""

from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF3, scale_to_max_gross
from tradingbot.utils.quantsmd_orders import fill_limit_buy, fill_stop_buy, ohlc_frames
from tradingbot.utils.runner import run_bot

UNIVERSE = list(ETF3)
FRACTION = 0.10
OFFSET = 0.02  # buy limit sits this far under the close, the buy stop this far over
HOLD_BARS = 10
OCO_EXPIRY_BARS = 5
REPLAY_BARS = 250
MONDAY = 0


def _replay(df: pd.DataFrame, fraction: float, offset: float, hold_bars: int) -> float:
    """Replay one ticker's OCO trade over the last ``REPLAY_BARS`` bars.

    Returns the position weight after the final bar: ``fraction`` while held,
    else ``0.0``. There are no indicators, so every bar is a decision bar.
    """
    closes = df["close"].to_numpy(dtype=float)
    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    weekdays = df.index.weekday

    pos = 0.0
    entry_bar: int | None = None
    oco: dict | None = None  # the live OCO pair, or None when no pair is live
    for i in range(max(0, len(df) - REPLAY_BARS), len(df)):
        # (a) the OCO placed on an earlier bar. The limit is tested before the stop.
        if oco is not None and i > oco["submit"]:
            fill = fill_limit_buy(opens[i], lows[i], oco["limit"])
            if fill is None:
                fill = fill_stop_buy(opens[i], highs[i], oco["stop"])
            if fill is not None:
                pos = fraction
                oco = None
            elif i - oco["submit"] >= OCO_EXPIRY_BARS:
                oco = None
        # (b) decisions at the close.
        if pos > 0.0:
            if entry_bar is None:
                entry_bar = i
            if i - entry_bar >= hold_bars:
                pos = 0.0
                entry_bar = None
                continue
        else:
            entry_bar = None
            if oco is None and weekdays[i] == MONDAY:
                oco = {
                    "limit": round(closes[i] * (1.0 - offset), 2),
                    "stop": round(closes[i] * (1.0 + offset), 2),
                    "submit": i,
                }
    return pos


def compute_weights(
    ohlc: dict[str, pd.DataFrame],
    fraction: float = FRACTION,
    offset: float = OFFSET,
    hold_bars: int = HOLD_BARS,
) -> dict[str, float]:
    """Return ``{ticker: fraction}`` for each ticker whose OCO trade is still open.

    ``ohlc`` maps tickers to open/high/low/close frames up to the current bar.
    A missing ticker or a short history contributes nothing. The result is
    scaled to a gross of at most 1.0 and is ``{}`` when nothing is held.
    """
    weights: dict[str, float] = {}
    for ticker in UNIVERSE:
        df = ohlc.get(ticker)
        if df is None or df.empty:
            continue
        weights[ticker] = _replay(df, fraction, offset, hold_bars)
    return scale_to_max_gross(weights, 1.0)


class QuantsmdOcoDipOrBreakoutBot(Bot):
    """
    Monday OCO pair: a buy limit 2% under the close or a buy stop 2% over it.

    Args:
        fraction: Fixed share of NAV for each open position.
        offset: Distance of the limit (below) and stop (above) from the close.
        hold_bars: Bars after entry before the position is sold at the market.

    The OCO orders are replayed over the last 250 bars on every run. It never
    returns ``None``.
    """

    # The replay needs more than the default 1y backtest window.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "offset": [0.01, 0.02, 0.03],
        "hold_bars": [5, 10, 20],
    }

    def __init__(
        self,
        fraction: float = FRACTION,
        offset: float = OFFSET,
        hold_bars: int = HOLD_BARS,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdOcoDipOrBreakoutBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must exceed the replay length so the OCO history is complete.
            period="2y",
            fraction=fraction,
            offset=offset,
            hold_bars=hold_bars,
            **kwargs,
        )
        self.fraction = fraction
        self.offset = offset
        self.hold_bars = hold_bars

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the OCO book for the current bar."""
        ohlc = ohlc_frames(self.datas, self.tradeable_tickers)
        return compute_weights(ohlc, self.fraction, self.offset, self.hold_bars)


if __name__ == "__main__":
    run_bot(QuantsmdOcoDipOrBreakoutBot)
