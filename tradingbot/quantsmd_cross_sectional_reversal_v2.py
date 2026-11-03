"""
Variant of tradingbot/quantsmd_cross_sectional_reversal.py

Weekly cross-sectional reversal across the nine S&P sector ETFs, with a rank buffer
so a holding is only sold once it drifts out of the weakest ``n_long + buffer``
sectors. On Fridays, rank the sectors by their trailing 21-bar return and hold the
``n_long`` weakest (the losers) at ``0.5 / n_long`` each. The holdings are replayed
over the last ``REPLAY_WEEKS`` Fridays, so a sector that was bought as a loser is
kept while it stays within ``n_long + buffer`` of the weakest, and open slots are
filled by the best-ranked names not yet held.

Departures from quantsmd_cross_sectional_reversal:
  * Rank buffer: a holding is kept while its rank is within ``n_long + BUFFER``
    (BUFFER = 2), instead of being re-picked from the top ``n_long`` every week.
    ``buffer=0`` reproduces the original book exactly.
  * The holdings are replayed over the last ``REPLAY_WEEKS`` Fridays so the buffer
    has a state to act on. Stateless replay is the only option here, since the bot
    keeps no memory between runs.
  * Long-only, 50% gross, and the Friday gate are unchanged from the original.

Universe: SECTORS9 (XLB, XLE, XLF, XLI, XLK, XLP, XLU, XLV, XLY).
"""

from typing import ClassVar

import numpy as np
import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import SECTORS9, closes_frame, is_weekday_bar
from tradingbot.utils.runner import run_bot

UNIVERSE = list(SECTORS9)
LOOKBACK = 21
N_LONG = 3
BUFFER = 2
GROSS = 0.5
MIN_NAMES = 6
FRIDAY = 4
REPLAY_WEEKS = 26


def _ranking(closes: pd.DataFrame, lookback: int) -> list[str] | None:
    """Return every valid name ordered weakest trailing return first.

    Returns ``None`` when there are fewer than ``lookback + 1`` rows or fewer than
    ``MIN_NAMES`` names have a valid return on the last bar.
    """
    if len(closes) < lookback + 1:
        return None
    past = closes.iloc[-1 - lookback]
    now = closes.iloc[-1]
    ret = (now / past - 1.0).replace([np.inf, -np.inf], np.nan).dropna()
    if len(ret) < MIN_NAMES:
        return None
    return [str(t) for t in ret.sort_values(ascending=True, kind="stable").index]


def _update_holdings(
    holdings: list[str],
    ranking: list[str],
    n_long: int,
    buffer: int,
) -> list[str]:
    """Advance the holdings by one rebalance.

    A current holding is kept while its rank (1 = weakest) is within
    ``n_long + buffer``; if more than ``n_long`` survive, the weakest-ranked ones
    win. Open slots are then filled by the best-ranked names not already held.
    """
    rank = {t: i + 1 for i, t in enumerate(ranking)}
    limit = n_long + buffer
    kept = [t for t in holdings if t in rank and rank[t] <= limit]
    kept = sorted(kept, key=lambda t: rank[t])[:n_long]
    for t in ranking:
        if len(kept) >= n_long:
            break
        if t not in kept:
            kept.append(t)
    return kept


def compute_weights(
    closes: pd.DataFrame,
    lookback: int = LOOKBACK,
    n_long: int = N_LONG,
    buffer: int = BUFFER,
) -> dict[str, float] | None:
    """Return ``0.5 / n_long`` each on the buffered loser book, on Fridays.

    ``closes`` holds adjusted closes (ascending index, one column per ticker),
    history up to and including the current bar. Returns ``None`` when the last
    bar is not a Friday (no rebalance). Returns ``{}`` when the current bar has
    fewer than ``lookback + 1`` rows or fewer than ``MIN_NAMES`` valid names.

    The book is replayed over the last ``REPLAY_WEEKS`` Fridays of ``closes``,
    oldest first, starting from an empty book. Weeks without a valid ranking
    leave the holdings unchanged.
    """
    if not is_weekday_bar(closes.index, FRIDAY):
        return None
    if _ranking(closes, lookback) is None:
        return {}
    fridays = [i for i, ts in enumerate(closes.index) if pd.Timestamp(ts).weekday() == FRIDAY]
    holdings: list[str] = []
    for i in fridays[-REPLAY_WEEKS:]:
        ranking = _ranking(closes.iloc[: i + 1], lookback)
        if ranking is None:
            continue
        holdings = _update_holdings(holdings, ranking, n_long, buffer)
    if not holdings:
        return {}
    return dict.fromkeys(holdings, GROSS / n_long)


class QuantsmdCrossSectionalReversalV2Bot(Bot):
    """
    Long the weakest sector ETFs by trailing 21-bar return, rebalanced on Fridays,
    keeping a holding while it stays within ``n_long + buffer`` of the weakest.

    Args:
        lookback: Trading bars in the return lookback (source: 21).
        n_long: Number of weakest sectors held (the source's long leg: 3).
        buffer: Extra ranks a holding may slip before it is sold (0 = original).

    Rebalances on Fridays only; every other bar returns ``None`` (book untouched).
    """

    param_grid: ClassVar[dict] = {
        "buffer": [0, 2, 3],
    }
    # Needs more than the default 1y backtest window to be meaningful.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    def __init__(
        self,
        lookback: int = LOOKBACK,
        n_long: int = N_LONG,
        buffer: int = BUFFER,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdCrossSectionalReversalV2Bot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch must cover the 26-week holdings replay plus the lookback,
            # or the replayed book starts short and the buffer has nothing to hold.
            period="1y",
            lookback=lookback,
            n_long=n_long,
            buffer=buffer,
            **kwargs,
        )
        self.lookback = lookback
        self.n_long = n_long
        self.buffer = buffer

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float] | None:
        """Return the buffered loser book on Fridays, ``None`` on every other bar."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes, self.lookback, self.n_long, self.buffer)


if __name__ == "__main__":
    run_bot(QuantsmdCrossSectionalReversalV2Bot)
