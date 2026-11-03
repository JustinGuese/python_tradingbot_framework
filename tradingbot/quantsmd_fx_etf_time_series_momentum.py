"""
Variant of tradingbot/quantsmd_fx_time_series_momentum.py

Monthly time-series momentum on six currency ETFs. On the first bar of each
calendar month, each ETF's sign is the sign of its trailing price return over
the lookback, and its size is the inverse of its 63-bar annualised volatility.
Only the names with a positive sign are held, weighted by inverse volatility and
normalised so the held book sums to 1.0.

Departures from the spot FX original:
  * Long-only, as in the original's effective book: negative-sign ETFs are
    dropped rather than shorted, and the book is scaled to full gross when any
    name is held.
  * ETF proxies for carry. Spot FX pairs have no carry, so the original lost on
    the rate differential it cannot see. Currency ETFs hold interest-bearing
    deposits, so their prices carry the rate differential.
  * History runs from 2006, the first full year of the currency ETFs, rather
    than the spot pairs' history.
  * The source's rebalance engine is replaced by the framework's no-trade band.

Universe: FXETF6 (FXE, FXB, FXA, FXY, FXC, FXF).
"""

from typing import ClassVar

import numpy as np
import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import closes_frame, period_rolled
from tradingbot.utils.runner import run_bot

FXETF6 = ["FXE", "FXB", "FXA", "FXY", "FXC", "FXF"]
DEFAULT_LOOKBACK = 126
VOL_WINDOW = 63
TRADING_DAYS = 252


def compute_weights(closes: pd.DataFrame, lookback: int = DEFAULT_LOOKBACK) -> dict[str, float] | None:
    """Return long-only time-series momentum weights for the currency ETFs.

    ``closes`` holds ETF closes (ascending index, one column per ETF), history
    up to and including the current bar. Returns ``None`` when the last bar is
    not the first bar of a new month, meaning "do not rebalance". Returns ``{}``
    when there are fewer than ``lookback + 1`` rows, or when no ETF has a positive
    signal with usable volatility. Otherwise each ETF with a positive sign is
    returned at ``inv_vol / sum(inv_vol over positive-sign ETFs)``, so the book
    sums to 1.0.
    """
    if not period_rolled(closes.index, "M"):
        return None
    if len(closes) < lookback + 1:
        return {}
    momentum = closes.iloc[-1] / closes.iloc[-1 - lookback] - 1.0
    returns = closes / closes.shift(1) - 1.0
    vol = returns.rolling(VOL_WINDOW).std().iloc[-1] * np.sqrt(TRADING_DAYS)
    inv = (1.0 / vol).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    positive = (momentum > 0.0) & (inv > 0.0)
    raw = inv[positive]
    total = float(raw.sum())
    if not np.isfinite(total) or total <= 0.0:
        return {}
    return {str(t): float(w) / total for t, w in raw.items()}


class QuantsmdFxEtfTimeSeriesMomentumBot(Bot):
    """
    Hold the currency ETFs with positive time-series momentum, sized by inverse volatility.

    Args:
        lookback: Trading bars in the momentum lookback (default: 126).
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
            "QuantsmdFxEtfTimeSeriesMomentumBot",
            tickers=list(FXETF6),
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
    run_bot(QuantsmdFxEtfTimeSeriesMomentumBot)
