"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-weights-04-quarterly-dual-momentum
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_04_quarterly_dual_momentum.py (Apache-2.0, (c) 2026 QuantJourney)

Quarterly momentum rotation across eight ETFs (SPY, EFA, EEM, TLT, IEF, GLD, DBC, VNQ).
Each ETF is scored by its trailing 252-bar return. The top two with a positive
return are held, each at 50%. Whatever is not picked stays in cash. The book is
only evaluated on the first bar of each calendar quarter; every other bar
returns ``None`` (no rebalance).

Departures from source:
- The source's rebalance engine is replaced by the framework's own no-trade band.
"""

from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF8, closes_frame, equal_weight, period_rolled
from tradingbot.utils.quantsmd_common import top_n as pick_top
from tradingbot.utils.runner import run_bot

UNIVERSE = list(ETF8)
LOOKBACK = 252
TOP_N = 2
CAP = 0.50


def compute_weights(
    closes: pd.DataFrame,
    lookback: int,
    top_n: int,
    cap: float,
) -> dict[str, float] | None:
    """Equal-weight the ``top_n`` ETFs with the best positive trailing return, on quarter starts.

    ``closes`` holds history up to and including the current bar, with one
    column per ticker. Returns ``None`` unless the last bar starts a new
    calendar quarter. Fewer than ``lookback + 1`` rows returns ``{}``. The
    score is ``close / close[-1 - lookback] - 1`` on the last bar; only strictly
    positive scores can be picked. Each pick gets ``min(1/len(picks), cap)``,
    so with one pick it gets ``cap``.
    """
    if not period_rolled(closes.index, "Q"):
        return None
    if len(closes) < lookback + 1:
        return {}
    momentum = closes.iloc[-1] / closes.iloc[-1 - lookback] - 1
    picks = pick_top(momentum, top_n, positive_only=True)
    return equal_weight(picks, cap)


class QuantsmdQuarterlyDualMomentumBot(Bot):
    """
    Quarterly 12-month momentum on eight ETFs, top two positive names at 50% each.

    Args:
        lookback: Trailing return window in daily bars.
        top_n: Number of ETFs to hold. Only positive-return ETFs are eligible.
        cap: Maximum weight for any single ETF.

    Rebalances on the first bar of each quarter and returns ``None`` otherwise.
    """

    # A 252-bar lookback needs more than the default 1y backtest window.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "lookback": [126, 252],
        "top_n": [2, 3],
    }

    def __init__(
        self,
        lookback: int = LOOKBACK,
        top_n: int = TOP_N,
        cap: float = CAP,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdQuarterlyDualMomentumBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must exceed the lookback, or no ETF is ever
            # evaluable and the bot holds cash without warning.
            period="3y",
            lookback=lookback,
            top_n=top_n,
            cap=cap,
            **kwargs,
        )
        self.lookback = lookback
        self.top_n = top_n
        self.cap = cap

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float] | None:
        """Return the momentum book on a quarter start, ``None`` on every other bar."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes, self.lookback, self.top_n, self.cap)


if __name__ == "__main__":
    run_bot(QuantsmdQuarterlyDualMomentumBot)
