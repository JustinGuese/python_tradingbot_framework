"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-orders-07-stop-limit-breakout
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_orders_07_stop_limit_breakout.py (Apache-2.0, (c) 2026 QuantJourney)

Stop-limit breakout entry on the three core ETFs (SPY, QQQ, IWM). While flat, a
buy stop-limit sits at a stop 0.2% above the highest close of the 20 bars before
the current bar, with a limit 0.4% above that stop. It is active for 5 bars. A
triggered order that does not fill stays live as a buy limit until it expires.
A position is closed at the close once it has been held for 12 bars. The
fraction is 10% of NAV per open position.

Departures from source:
- No resting orders. A CronJob keeps no state and cannot leave a stop-limit at a
  broker, so the order is replayed statelessly over the last 250 daily bars,
  with the OHLC fill model in ``tradingbot/utils/quantsmd_orders.py``. Market
  orders act at the decision bar's close. An order that fills on bar j changes
  the position on bar j. The framework records the bar's close as the execution
  price, not the intrabar fill price.
- The time stop is a market sell at the close. Position size is a fixed 10% of
  NAV, as in the source. The framework's no-trade band replaces the source's
  rebalancing.
"""

from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF3, scale_to_max_gross
from tradingbot.utils.quantsmd_orders import fill_limit_buy, fill_stop_limit_buy, ohlc_frames
from tradingbot.utils.runner import run_bot

UNIVERSE = list(ETF3)
HOLD = 12
FRACTION = 0.10
LOOKBACK = 20
STOP_BUFFER = 0.002
LIMIT_BUFFER = 0.004
EXPIRES_AFTER_BARS = 5
REPLAY_BARS = 250


def _replay(df: pd.DataFrame, hold: int, fraction: float) -> float:
    """Replay one ticker's stop-limit breakout entries over the last ``REPLAY_BARS`` bars.

    Returns the position weight after the final bar: ``fraction`` while held,
    else ``0.0``. Bars with fewer than ``LOOKBACK + 1`` prior rows are skipped.
    """
    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    n = len(df)

    pos = 0.0
    entry_bar = -1
    pending: dict | None = None
    for i in range(max(LOOKBACK + 1, n - REPLAY_BARS), n):
        # (a) a stop-limit submitted on an earlier bar is tested on this bar.
        if pending is not None and i > pending["submit"]:
            if pending["triggered"]:
                # Once triggered, the order is a live buy limit.
                fill = fill_limit_buy(opens[i], lows[i], pending["limit"])
            else:
                fill, triggered = fill_stop_limit_buy(opens[i], highs[i], lows[i], pending["stop"], pending["limit"])
                if triggered:
                    pending["triggered"] = True
            if fill is not None:
                pos = fraction
                pending = None
            elif i >= pending["submit"] + EXPIRES_AFTER_BARS:
                pending = None
        # (b) decisions at the close.
        if pos > 0.0 and entry_bar < 0:
            entry_bar = i
        if pos > 0.0 and i - entry_bar >= hold:
            pos = 0.0
            pending = None
            entry_bar = -1
            continue
        if pos == 0.0 and pending is None:
            prior_high = closes[i - LOOKBACK : i].max()
            stop = round(prior_high * (1.0 + STOP_BUFFER), 2)
            limit = round(stop * (1.0 + LIMIT_BUFFER), 2)
            pending = {"submit": i, "stop": stop, "limit": limit, "triggered": False}
    return pos


def compute_weights(
    ohlc: dict[str, pd.DataFrame],
    hold: int = HOLD,
    fraction: float = FRACTION,
) -> dict[str, float]:
    """Return ``{ticker: fraction}`` for each ticker whose breakout position is held.

    ``ohlc`` maps tickers to open/high/low/close frames up to the current bar.
    A missing ticker or a short history contributes nothing. The result is
    scaled to a gross of at most 1.0 and is ``{}`` when nothing is held.
    """
    weights: dict[str, float] = {}
    for ticker in UNIVERSE:
        df = ohlc.get(ticker)
        if df is None or df.empty:
            continue
        weights[ticker] = _replay(df, hold, fraction)
    return scale_to_max_gross(weights, 1.0)


class QuantsmdStopLimitBreakoutBot(Bot):
    """
    Buy stop-limit breakout entries above the 20-bar high, closed after a 12-bar hold.

    Args:
        hold: Bars a position is held before the time-stop market sell.
        fraction: Fixed share of NAV for each open position.

    The stop-limit order is replayed over the last 250 bars on every run. It
    never returns ``None``.
    """

    # The 20-bar breakout high and the replay need more than the default 1y window.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "hold": [6, 12, 24],
    }

    def __init__(
        self,
        hold: int = HOLD,
        fraction: float = FRACTION,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdStopLimitBreakoutBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must exceed the 20-bar lookback plus the replay length.
            period="2y",
            hold=hold,
            fraction=fraction,
            **kwargs,
        )
        self.hold = hold
        self.fraction = fraction

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the stop-limit breakout book for the current bar."""
        ohlc = ohlc_frames(self.datas, self.tradeable_tickers)
        return compute_weights(ohlc, self.hold, self.fraction)


if __name__ == "__main__":
    run_bot(QuantsmdStopLimitBreakoutBot)
