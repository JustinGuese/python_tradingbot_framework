"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-weights-03-weekly-rsi-reversion
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_03_weekly_rsi_reversion.py (Apache-2.0, (c) 2026 QuantJourney)

Weekly RSI mean reversion across the nine S&P sectors. Each sector's 14-day RSI
is tracked with hysteresis: it is bought when RSI falls below 35 and held until
RSI rises above 60. Weights are decided on Fridays only. Held sectors are
equal-weighted, each capped at 25%, and the remainder stays in cash.

The source keeps this state per instrument across bars. The bot holds no state
between CronJob runs, so the RSI state machine is replayed daily over the last
250 bars on every call. A position opened earlier than that window is not
carried in.

Departures from source: none in the signal. Weights are already long-only with
no leverage, so they are followed as written.
"""

from typing import ClassVar

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import SECTORS9, closes_frame, equal_weight, is_weekday_bar
from tradingbot.utils.runner import run_bot

UNIVERSE = list(SECTORS9)
RSI_PERIOD = 14
ENTRY = 35.0
EXIT = 60.0
CAP = 0.25
REPLAY = 250
FRIDAY = 4


def _held_at_end(rsi: np.ndarray, entry: float, exit_: float, replay: int) -> bool:
    """Walk the RSI hold flag forward over the last ``replay`` bars and return it.

    Rules per bar: a NaN RSI clears the flag. A flat position enters when RSI is
    below ``entry``. A held position exits when RSI is above ``exit_``. Otherwise
    the flag is unchanged (hysteresis).
    """
    held = False
    start = max(0, len(rsi) - replay)
    for i in range(start, len(rsi)):
        value = rsi[i]
        if np.isnan(value):
            held = False
        elif not held and value < entry:
            held = True
        elif held and value > exit_:
            held = False
    return held


def compute_weights(
    closes: pd.DataFrame,
    rsi_period: int,
    entry: float,
    exit_: float,
    cap: float,
    replay: int = REPLAY,
) -> dict[str, float] | None:
    """Equal-weight the sectors whose RSI hold flag is set on the last bar, on Fridays.

    ``closes`` holds history up to and including the current bar, with one
    column per ticker. Returns ``None`` when the last bar is not a Friday (no
    rebalance). Fewer than ``rsi_period + 2`` rows returns ``{}``. RSI is computed
    on the tail of ``replay + rsi_period + 5`` rows, and the hold flag is replayed
    daily over the last ``replay`` of them, so the result does not depend on older
    history. Each active weight is ``min(1/len(active), cap)``, so any clipped
    excess stays in cash.
    """
    if not is_weekday_bar(closes.index, FRIDAY):
        return None
    if len(closes) < rsi_period + 2:
        return {}
    tail = closes.tail(replay + rsi_period + 5)
    active = []
    for ticker in closes.columns:
        series = tail[ticker].astype(float)
        rsi = RSIIndicator(close=series, window=rsi_period).rsi()
        held = _held_at_end(rsi.to_numpy(dtype=float), entry, exit_, replay)
        if held:
            active.append(str(ticker))
    return equal_weight(active, cap)


class QuantsmdWeeklyRsiReversionBot(Bot):
    """
    Weekly RSI reversion on the nine S&P sector ETFs, equal weight with a 25% cap.

    Args:
        rsi_period: RSI lookback in daily bars.
        entry: Buy when RSI falls below this level.
        exit: Sell when RSI rises above this level.
        cap: Maximum weight for any single sector.

    Rebalances on Fridays only; every other bar returns ``None`` (book untouched).
    """

    # Needs more than the default 1y backtest window to be meaningful.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "entry": [30, 35, 40],
        "exit": [55, 60, 65],
    }

    def __init__(
        self,
        rsi_period: int = RSI_PERIOD,
        entry: float = ENTRY,
        exit: float = EXIT,
        cap: float = CAP,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdWeeklyRsiReversionBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must exceed the RSI warmup plus the replay length, or the
            # hold flag cannot be rebuilt and the bot quietly sits in cash.
            period="2y",
            rsi_period=rsi_period,
            entry=entry,
            exit=exit,
            cap=cap,
            **kwargs,
        )
        self.rsi_period = rsi_period
        self.entry = entry
        self.exit = exit
        self.cap = cap

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float] | None:
        """Return the sector book on Fridays, ``None`` on every other bar."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes, self.rsi_period, self.entry, self.exit, self.cap)


if __name__ == "__main__":
    run_bot(QuantsmdWeeklyRsiReversionBot)
