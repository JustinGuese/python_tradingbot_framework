"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-weights-23-fx-time-series-momentum
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_23_fx_time_series_momentum.py (Apache-2.0, (c) 2026 QuantJourney)

Monthly time-series momentum on four FX spot pairs. On the first bar of each
calendar month, each pair's sign is the sign of its trailing price return over
the lookback, and its size is the inverse of its 63-bar annualised volatility.
Raw weights are sign * inverse vol, normalised by the sum of their absolute
values over all four pairs. Only the long legs are held, so the shorted pairs'
share stays in cash.

Departures from source:
  * Long-only: pairs with a negative sign are dropped rather than shorted. The
    book is no longer market-neutral, and the short share stays in cash.
  * Paper only. Prices are spot FX price returns, with no carry or swap.
  * The source's rebalance engine is replaced by the framework's no-trade band.

Universe: FX4 (EURUSD=X, GBPUSD=X, AUDUSD=X, NZDUSD=X).
"""

from typing import ClassVar

import numpy as np
import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import FX4, closes_frame, period_rolled
from tradingbot.utils.runner import run_bot

DEFAULT_LOOKBACK = 126
VOL_WINDOW = 63
TRADING_DAYS = 252


def compute_weights(closes: pd.DataFrame, lookback: int = DEFAULT_LOOKBACK) -> dict[str, float] | None:
    """Return long-only time-series momentum weights for the FX pairs.

    ``closes`` holds spot closes (ascending index, one column per pair), history
    up to and including the current bar. Returns ``None`` when the last bar is
    not the first bar of a new month, meaning "do not rebalance". Returns ``{}``
    when there are fewer than ``lookback + 1`` rows, or when no pair has a usable
    signal and volatility. Otherwise each pair with a positive weight is returned
    at ``sign * inv_vol / sum(|raw|)``, where the sum runs over all pairs.
    """
    if not period_rolled(closes.index, "M"):
        return None
    if len(closes) < lookback + 1:
        return {}
    momentum = closes.iloc[-1] / closes.iloc[-1 - lookback] - 1.0
    sign = np.sign(momentum).fillna(0.0)
    returns = closes / closes.shift(1) - 1.0
    vol = returns.rolling(VOL_WINDOW).std().iloc[-1] * np.sqrt(TRADING_DAYS)
    inv = (1.0 / vol).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    raw = sign * inv
    denom = float(raw.abs().sum())
    if not np.isfinite(denom) or denom <= 0.0:
        return {}
    weights = raw / denom
    return {str(t): float(w) for t, w in weights.items() if w > 0.0}


class QuantsmdFxTimeSeriesMomentumBot(Bot):
    """
    Hold the FX pairs with positive time-series momentum, sized by inverse volatility.

    Args:
        lookback: Trading bars in the momentum lookback (source: 126).
    """

    param_grid: ClassVar[dict] = {
        "lookback": [63, 126, 252],
    }
    # A 126-bar lookback plus a 63-bar vol window needs more than the default 1y backtest window.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    def __init__(
        self,
        lookback: int = DEFAULT_LOOKBACK,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdFxTimeSeriesMomentumBot",
            tickers=list(FX4),
            interval="1d",
            period="2y",
            lookback=lookback,
            **kwargs,
        )
        self.lookback = lookback

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float] | None:
        """Delegate to compute_weights on the closes of the tradeable universe."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes, self.lookback)


if __name__ == "__main__":
    run_bot(QuantsmdFxTimeSeriesMomentumBot)
