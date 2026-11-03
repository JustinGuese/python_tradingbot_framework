"""
Variant of tradingbot/quantsmd_cross_sectional_momentum.py

Monthly cross-sectional momentum across the nine S&P sector ETFs. On the first bar
of each calendar month, skip the most recent 21 bars, rank the sectors by their
trailing 252-bar return measured up to that skip, drop the sectors whose 63-bar
volatility is far above the cross-section, and hold the top 3 at 0.5 / 3 each, so
50% of NAV is invested and the rest stays in cash.

Departures from quantsmd_cross_sectional_momentum:
  * Skip-month: momentum is ``close[-1-SKIP] / close[-1-SKIP-lookback] - 1`` with
    SKIP = 21, so the most recent month (short-term reversal) is ignored.
    ``skip=0`` reproduces the original ranking.
  * Volatility filter: a name whose 63-bar annualised volatility exceeds
    VOL_MULT (1.5) times the cross-sectional median is dropped before the top-n pick.
    Names with fewer than 63 returns have no volatility and are not filtered.
  * Long-only, 50% gross, the monthly gate and the sizing are unchanged.

Universe: SECTORS9 (XLB, XLE, XLF, XLI, XLK, XLP, XLU, XLV, XLY).
"""

from typing import ClassVar

import numpy as np
import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import (
    SECTORS9,
    annualized_vol,
    closes_frame,
    period_rolled,
    top_n,
)
from tradingbot.utils.runner import run_bot

UNIVERSE = list(SECTORS9)
LOOKBACK = 252
N_LONG = 3
GROSS = 0.5
MIN_NAMES = 6
SKIP = 21
VOL_WINDOW = 63
VOL_MULT = 1.5


def compute_weights(
    closes: pd.DataFrame,
    lookback: int = LOOKBACK,
    n_long: int = N_LONG,
    skip: int = SKIP,
) -> dict[str, float] | None:
    """Return 0.5 / n_long each on the n_long strongest low-volatility sectors.

    ``closes`` holds adjusted closes (ascending index, one column per ticker),
    history up to and including the current bar. Returns ``None`` when the last
    bar is not the first bar of a new month (no rebalance). Returns ``{}`` when
    there are fewer than ``lookback + skip + 1`` rows or fewer than ``MIN_NAMES``
    names have a valid momentum.

    Momentum is ``close[-1-skip] / close[-1-skip-lookback] - 1``. Among the names
    with a valid momentum, any whose ``VOL_WINDOW``-bar annualised volatility is
    above ``VOL_MULT`` times the median volatility is dropped. The median runs over
    the names that have a volatility; names without one are kept.
    """
    if not period_rolled(closes.index, "M"):
        return None
    if len(closes) < lookback + skip + 1:
        return {}
    past = closes.iloc[-1 - skip - lookback]
    now = closes.iloc[-1 - skip]
    momentum = (now / past - 1.0).replace([np.inf, -np.inf], np.nan).dropna()
    if len(momentum) < MIN_NAMES:
        return {}
    vol = annualized_vol(closes, VOL_WINDOW).reindex(momentum.index)
    finite_vol = vol.dropna()
    if not finite_vol.empty:
        cutoff = VOL_MULT * float(finite_vol.median())
        keep = vol.isna() | (vol <= cutoff)
        momentum = momentum[keep]
    picks = top_n(momentum, n_long)
    if not picks:
        return {}
    return dict.fromkeys(picks, GROSS / n_long)


class QuantsmdCrossSectionalMomentumV2Bot(Bot):
    """
    Long the strongest low-volatility sector ETFs by trailing 252-bar return,
    skipping the latest 21 bars, rebalanced monthly.

    Args:
        lookback: Trading bars in the momentum lookback (source: 252).
        n_long: Number of sectors held (the source's long leg: 3).
        skip: Most recent bars excluded from the momentum window (0 = original).

    Rebalances on the first bar of each month; every other bar returns ``None``.
    """

    param_grid: ClassVar[dict] = {
        "skip": [0, 21],
    }
    # A 252-bar lookback needs more than the default 1y backtest window.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    def __init__(
        self,
        lookback: int = LOOKBACK,
        n_long: int = N_LONG,
        skip: int = SKIP,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdCrossSectionalMomentumV2Bot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch must exceed lookback + skip, or momentum is undefined and
            # the bot quietly sits in cash.
            period="3y",
            lookback=lookback,
            n_long=n_long,
            skip=skip,
            **kwargs,
        )
        self.lookback = lookback
        self.n_long = n_long
        self.skip = skip

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float] | None:
        """Return the momentum book on month-first bars, ``None`` otherwise."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes, self.lookback, self.n_long, self.skip)


if __name__ == "__main__":
    run_bot(QuantsmdCrossSectionalMomentumV2Bot)
