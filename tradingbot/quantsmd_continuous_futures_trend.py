"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-weights-25-continuous-futures-trend
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_25_continuous_futures_trend.py (Apache-2.0, (c) 2026 QuantJourney)

Monthly continuous-futures trend: sign of (fast MA - slow MA), inverse-vol sized.
Each name's signal is +1 when the fast moving average is above the slow one, else
-1; weights are 1/realised-vol, normalised so the gross book is 1.0.

Departures from source: long-only. The source is long/short unit-gross; here
every would-be short leg is left in cash instead, so the book sums to less than
1.0 whenever some names are in downtrends. No leverage and no roll model.
"""

from __future__ import annotations

import math
from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import closes_frame, period_rolled
from tradingbot.utils.runner import run_bot

UNIVERSE = ["MES=F", "MNQ=F", "ZN=F", "CL=F", "GC=F", "ZC=F"]
FAST = 63
SLOW = 252
VOL_WINDOW = 63
_TRADING_DAYS = 252


def compute_weights(
    closes: pd.DataFrame,
    fast: int = FAST,
    slow: int = SLOW,
    vol_window: int = VOL_WINDOW,
) -> dict[str, float] | None:
    """Long-only inverse-vol trend weights, rebalanced on the first bar of a month.

    ``closes`` is a wide adjusted-close frame (index ascending, one column per
    ticker, history up to and including the current bar). Returns ``None`` when
    the last bar is not the first of a new month, and ``{}`` when there is not
    enough history or no trend is up.

    Weight for a long leg is ``(1/vol_i) / sum_j(1/vol_j)``, where the denominator
    runs over every valid ticker, long or would-be short, so that short legs are
    removed from the book rather than re-levelled onto the longs.
    """
    if not period_rolled(closes.index, "M"):
        return None
    if len(closes) < slow + 1:
        return {}

    fast_ma = closes.rolling(fast, min_periods=fast).mean().iloc[-1]
    slow_ma = closes.rolling(slow, min_periods=slow).mean().iloc[-1]
    vol = closes.pct_change().rolling(vol_window, min_periods=vol_window).std().iloc[-1] * math.sqrt(_TRADING_DAYS)

    valid = fast_ma.notna() & slow_ma.notna() & vol.notna() & (vol > 0)
    if not valid.any():
        return {}
    rest = 1.0 / vol[valid]
    denominator = float(rest.sum())
    if denominator <= 0:
        return {}

    weights: dict[str, float] = {}
    for ticker in rest.index:
        if fast_ma[ticker] > slow_ma[ticker]:
            weights[str(ticker)] = float(rest[ticker]) / denominator
    return weights


class QuantsmdContinuousFuturesTrendBot(Bot):
    """
    Monthly long-only trend on six continuous futures proxies, inverse-vol sized.

    Args:
        fast: Fast simple-moving-average window in trading days.
        slow: Slow simple-moving-average window in trading days. Must exceed fast.
        vol_window: Trading days in the realised-vol estimate used for sizing.

    Caveat: yfinance micro-futures (MES=F, MNQ=F) history starts around 2019, and
    the framework has no contract roll, multiplier or margin model. Treat this as a
    signal proxy on continuous price series, not as a futures-account replication.
    """

    param_grid: ClassVar[dict] = {
        "fast": [42, 63, 84],
        "slow": [189, 252],
        "vol_window": [42, 63],
    }

    # Needs more than a year of history before the first signal, so the default
    # one-year backtest window would leave almost nothing to evaluate.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    def __init__(
        self,
        fast: int = FAST,
        slow: int = SLOW,
        vol_window: int = VOL_WINDOW,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdContinuousFuturesTrendBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must exceed the slow window, or no signal can form.
            period="3y",
            fast=fast,
            slow=slow,
            vol_window=vol_window,
            **kwargs,
        )
        self.fast = fast
        self.slow = slow
        self.vol_window = vol_window

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float] | None:
        """Monthly long-only trend weights; ``None`` off the first bar of a month."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes, self.fast, self.slow, self.vol_window)


if __name__ == "__main__":
    run_bot(QuantsmdContinuousFuturesTrendBot)
