"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-weights-21-bollinger-reversion
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_21_bollinger_reversion.py (Apache-2.0, (c) 2026 QuantJourney)

Bollinger band mean reversion across the nine S&P sectors. A sector is bought
when its close falls below the lower band (mean minus n_std standard
deviations over a rolling window) and is held until its close rises back above
the rolling mean. Held sectors are equal-weighted, each capped at 25%, and the
remainder stays in cash.

The source keeps this state per instrument across bars. The bot holds no state
between CronJob runs, so the state is replayed over the last 250 bars on every
call. A position opened earlier than that window is not carried in.

Departures from source: none in the signal. Weights are already long-only with
no leverage, so they are followed as written.
"""

from typing import ClassVar

import numpy as np
import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import SECTORS9, closes_frame, equal_weight
from tradingbot.utils.runner import run_bot

UNIVERSE = list(SECTORS9)
WINDOW = 20
N_STD = 2.0
CAP = 0.25
REPLAY = 250


def _held_at_end(close: np.ndarray, mid: np.ndarray, lower: np.ndarray, replay: int) -> bool:
    """Walk the hold flag forward over the last ``replay`` bars and return it.

    Rules per bar: a NaN mid or lower band clears the flag. A flat position
    enters when close is below the lower band. A held position exits when close
    is above the mean. Otherwise the flag is unchanged (hysteresis).
    """
    held = False
    start = max(0, len(close) - replay)
    for i in range(start, len(close)):
        if np.isnan(mid[i]) or np.isnan(lower[i]):
            held = False
        elif not held and close[i] < lower[i]:
            held = True
        elif held and close[i] > mid[i]:
            held = False
    return held


def compute_weights(
    closes: pd.DataFrame,
    window: int,
    n_std: float,
    cap: float,
    replay: int = REPLAY,
) -> dict[str, float]:
    """Equal-weight the tickers whose Bollinger hold flag is set on the last bar.

    ``closes`` holds history up to and including the current bar, with one
    column per ticker. Fewer than ``window`` rows returns ``{}``. Only the last
    ``replay + window`` rows are used, and the hold flag is replayed over the
    last ``replay`` of them, so the result does not depend on older history.
    Each active weight is ``min(1/len(active), cap)``, so any clipped excess
    stays in cash.
    """
    if len(closes) < window:
        return {}
    tail = closes.tail(replay + window)
    mid = tail.rolling(window, min_periods=window).mean()
    std = tail.rolling(window, min_periods=window).std()
    lower = mid - n_std * std
    active = []
    for ticker in closes.columns:
        held = _held_at_end(
            tail[ticker].to_numpy(dtype=float),
            mid[ticker].to_numpy(dtype=float),
            lower[ticker].to_numpy(dtype=float),
            replay,
        )
        if held:
            active.append(str(ticker))
    return equal_weight(active, cap)


class QuantsmdBollingerReversionBot(Bot):
    """
    Bollinger band reversion on the nine S&P sector ETFs, equal weight with a 25% cap.

    Args:
        window: Rolling window in daily bars for the mean and standard deviation.
        n_std: Band width in standard deviations. Entry is below mean minus this.
        cap: Maximum weight for any single sector.

    The source's daily rebalance runs every bar. It never returns ``None``.
    """

    # Needs more than the default 1y backtest window to be meaningful.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "window": [10, 20, 30],
        "n_std": [1.5, 2.0, 2.5],
    }

    def __init__(
        self,
        window: int = WINDOW,
        n_std: float = N_STD,
        cap: float = CAP,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdBollingerReversionBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must exceed the window plus the replay length, or the
            # hold flag cannot be rebuilt and the bot quietly sits in cash.
            period="2y",
            window=window,
            n_std=n_std,
            cap=cap,
            **kwargs,
        )
        self.window = window
        self.n_std = n_std
        self.cap = cap

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the sector book for the current bar."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes, self.window, self.n_std, self.cap)


if __name__ == "__main__":
    run_bot(QuantsmdBollingerReversionBot)
