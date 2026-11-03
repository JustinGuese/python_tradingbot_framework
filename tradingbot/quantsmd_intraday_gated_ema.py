"""
Variant of quantsmd_intraday_1m_ema_scalp.py: a 5-minute EMA trend with cost gates.

Intraday 5-minute EMA trend on SPY, QQQ and IWM, one trade per ticker per session. The
last US session in the data is replayed bar by bar. A ticker is bought once, inside the
entry window, when its fast EMA is above its slow EMA by at least ``min_edge`` of price.
It is sold when the fast EMA falls below the slow one, and it never re-enters that
session. Held ETFs are equal-weighted, each capped at 34%. The book is flat into the
close.

Departures from quantsmd_intraday_1m_ema_scalp:
- Bar size 5 minutes, not 1 minute, so the 1-minute cost model no longer dominates.
- Cost gates: no entry in the first 15 minutes or the last 15 minutes of the session;
  an entry needs a fast-slow EMA gap of at least ``min_edge`` (0.15% of price by default);
  one entry per ticker per session. The source trades every crossover all day.
- Flat by the close: a position held at the cut-off bar is not carried overnight.
- Long-only, as the framework cannot short. Scheduled every 5 minutes during the session.
- Paper only. The backtest uses the framework's daily-cost model, so per-trade spreads
  and slippage on 5-minute bars are only approximated by the execution cost model.
"""

from typing import ClassVar

import numpy as np
import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF3, closes_frame, equal_weight
from tradingbot.utils.runner import run_bot

UNIVERSE = list(ETF3)
FAST = 9
SLOW = 21
MIN_EDGE = 0.0015
CAP = 0.34
SKIP_OPEN_MIN = 15
SKIP_CLOSE_MIN = 15

# US regular session in UTC minutes after midnight: 13:30 to 20:00.
SESSION_OPEN_MIN = 13 * 60 + 30
SESSION_CLOSE_MIN = 20 * 60


def compute_weights(
    closes: pd.DataFrame,
    fast: int = FAST,
    slow: int = SLOW,
    min_edge: float = MIN_EDGE,
    cap: float = CAP,
    skip_open_min: int = SKIP_OPEN_MIN,
    skip_close_min: int = SKIP_CLOSE_MIN,
) -> dict[str, float]:
    """Equal-weight the tickers still held at the last bar of the last session.

    ``closes`` holds history up to and including the current bar, one column per
    ticker, with a ``DatetimeIndex`` read as UTC. Only the bars of the last session
    date are replayed. Averages are ``ewm(span=n, adjust=False)`` over the whole
    history, so the session starts warm. Session bars are those from 13:30 up to
    (not including) 20:00 UTC.

    Per ticker, bar by bar, with ``entry_start = 13:30 + skip_open_min`` and
    ``entry_end = 20:00 - skip_close_min`` (both in UTC minutes):

    - if the ticker holds and ``fast < slow``, it is sold;
    - if it is flat, has not traded yet this session, the bar is in
      ``[entry_start, entry_end)`` and ``(fast - slow) / close >= min_edge``, it is bought.

    A ticker that has been sold never re-enters this session. Bars with a missing
    average or close are skipped. Held tickers get ``equal_weight(held, cap)``.

    Returns ``{}`` when there are no bars, when the last bar is at or after
    ``entry_end`` (flat into the close), or when nothing is held.
    """
    if closes.empty:
        return {}
    index = pd.DatetimeIndex(closes.index)
    last = index[-1]
    entry_start = SESSION_OPEN_MIN + skip_open_min
    entry_end = SESSION_CLOSE_MIN - skip_close_min
    if last.hour * 60 + last.minute >= entry_end:
        return {}

    minutes = np.asarray(index.hour * 60 + index.minute)
    same_day = np.asarray(index.normalize() == last.normalize())
    in_session = same_day & (minutes >= SESSION_OPEN_MIN) & (minutes < SESSION_CLOSE_MIN)
    positions = np.flatnonzero(in_session)
    if positions.size == 0:
        return {}

    ema_fast = closes.ewm(span=fast, adjust=False).mean().to_numpy(dtype=float)
    ema_slow = closes.ewm(span=slow, adjust=False).mean().to_numpy(dtype=float)
    prices = closes.to_numpy(dtype=float)

    held: list[str] = []
    for col, ticker in enumerate(closes.columns):
        holding = False
        traded = False
        for pos in positions:
            f, s, c = ema_fast[pos, col], ema_slow[pos, col], prices[pos, col]
            if not (np.isfinite(f) and np.isfinite(s) and np.isfinite(c)):
                continue
            if holding:
                if f < s:
                    holding = False
            elif not traded and entry_start <= minutes[pos] < entry_end and (f - s) / c >= min_edge:
                holding = True
                traded = True
        if holding:
            held.append(str(ticker))
    return equal_weight(held, cap)


class QuantsmdIntradayGatedEmaBot(Bot):
    """
    Intraday 5-minute EMA trend on SPY, QQQ and IWM with entry cost gates.

    Args:
        fast: Fast EMA span in 5-minute bars.
        slow: Slow EMA span in 5-minute bars. A position is sold when the fast
            average falls below this one.
        min_edge: Minimum (fast - slow) / close needed to enter.
        cap: Maximum weight for any single ETF.

    One entry per ticker per session, no entries in the first or last 15 minutes,
    and flat into the close. Never returns ``None``. Paper only.
    """

    # Yahoo serves 5-minute bars for about 60 days, which is the backtest window.
    BACKTEST_PERIOD: ClassVar[str | None] = "60d"

    param_grid: ClassVar[dict] = {
        "min_edge": [0.001, 0.0015, 0.003],
        "fast": [9, 12],
        "slow": [21, 34],
    }

    def __init__(
        self,
        fast: int = FAST,
        slow: int = SLOW,
        min_edge: float = MIN_EDGE,
        cap: float = CAP,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdIntradayGatedEmaBot",
            tickers=list(UNIVERSE),
            interval="5m",
            # Live fetch: about 5 sessions of 5-minute bars, well above the slow
            # window, so the averages are warm at the start of the session.
            period="5d",
            fast=fast,
            slow=slow,
            min_edge=min_edge,
            cap=cap,
            **kwargs,
        )
        self.fast = fast
        self.slow = slow
        self.min_edge = min_edge
        self.cap = cap

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the ETF book for the current 5-minute bar."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes, self.fast, self.slow, self.min_edge, self.cap)


if __name__ == "__main__":
    run_bot(QuantsmdIntradayGatedEmaBot)
