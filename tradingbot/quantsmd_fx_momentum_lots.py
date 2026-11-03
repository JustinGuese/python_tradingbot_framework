"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-orders-19-fx-momentum-lots
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_orders_19_fx_momentum_lots.py (Apache-2.0, (c) 2026 QuantJourney)

Trend-following FX lots on four spot pairs. A pair is held long when its close is
above its close 126 bars earlier. Its size is set by risk: 0.5% of NAV divided by
its 20-bar average true range, in price units, capped at 20% of NAV. The source
sizes whole lots; here the same sizing is kept as a weight without lot rounding.
Pairs with a falling trend are not held.

Departures from source:
  * Long-only: the short side is dropped. A falling pair gets no weight, so the
    book is not symmetric and the unused NAV stays in cash.
  * No lot rounding. The weight is the risk-sized notional as a fraction of NAV.
    The rule has no resting orders: it is re-evaluated on each daily close.
  * Paper only. Prices are spot FX price returns, with no carry or swap.

Universe: FX4 (EURUSD=X, GBPUSD=X, AUDUSD=X, NZDUSD=X).
"""

import math
from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import FX4, atr, scale_to_max_gross
from tradingbot.utils.quantsmd_orders import ohlc_frames
from tradingbot.utils.runner import run_bot

LOOKBACK = 126
ATR_WINDOW = 20
RISK = 0.005
CAP = 0.20
MAX_GROSS = 1.0


def compute_weights(
    ohlc: dict[str, pd.DataFrame],
    lookback: int = LOOKBACK,
    atr_window: int = ATR_WINDOW,
    risk: float = RISK,
    cap: float = CAP,
) -> dict[str, float]:
    """Risk-sized long weights for the pairs trending up over ``lookback`` bars.

    ``ohlc`` maps each ticker to a frame of ``open, high, low, close`` with one
    row per bar, ascending. A ticker needs at least ``lookback + 1`` rows. It is
    long when its last close is above the close ``lookback`` bars earlier, and
    otherwise omitted. Its weight is ``min(risk * close / atr, cap)``. A ticker
    whose ATR is NaN or not positive is skipped, because the size is undefined.
    The result goes through ``scale_to_max_gross`` at the end.
    """
    weights: dict[str, float] = {}
    for ticker, bars in ohlc.items():
        if len(bars) < lookback + 1:
            continue
        close = bars["close"].astype(float)
        last = float(close.iloc[-1])
        if last <= float(close.iloc[-1 - lookback]):
            continue
        atr_value = float(atr(bars["high"], bars["low"], close, atr_window).iloc[-1])
        if not math.isfinite(atr_value) or atr_value <= 0:
            continue
        weights[str(ticker)] = min(risk * last / atr_value, cap)
    return scale_to_max_gross(weights, MAX_GROSS)


class QuantsmdFxMomentumLotsBot(Bot):
    """
    Long the FX pairs in an uptrend, each sized at 0.5% of NAV per 20-bar ATR, capped at 20%.

    Args:
        lookback: Trend lookback in daily bars. A pair is long when its close is
            above the close this many bars earlier.

    Paper only. The short side is dropped, and no lot rounding is applied.
    """

    # The trend lookback needs more than the default 1y backtest window.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "lookback": [63, 126, 252],
    }

    def __init__(self, lookback: int = LOOKBACK, **kwargs):
        super().__init__(
            "QuantsmdFxMomentumLotsBot",
            tickers=list(FX4),
            interval="1d",
            # Live fetch: must exceed the lookback plus the ATR window, or no pair
            # is ever evaluable and the bot holds cash without warning.
            period="2y",
            lookback=lookback,
            **kwargs,
        )
        self.lookback = lookback

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the FX book for the current bar."""
        ohlc = ohlc_frames(self.datas, self.tradeable_tickers)
        return compute_weights(ohlc, self.lookback)


if __name__ == "__main__":
    run_bot(QuantsmdFxMomentumLotsBot)
