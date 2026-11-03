"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-weights-05-monthly-inverse-vol
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_05_monthly_inverse_vol.py (Apache-2.0, (c) 2026 QuantJourney)

Monthly inverse-volatility weights across the eight core ETFs. Realised
volatility is measured over the last 63 daily returns. Each ETF is weighted by
its inverse volatility, normalised to sum to one, then capped at 35%. The
clipped excess is not redistributed, so it stays in cash.

Departures from source: none. The source clips after normalising and does not
redistribute, and this bot does the same. The weights are long-only with no
leverage.
"""

from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF8, annualized_vol, closes_frame, period_rolled
from tradingbot.utils.runner import run_bot

UNIVERSE = list(ETF8)
LOOKBACK = 63
CAP = 0.35


def compute_weights(
    closes: pd.DataFrame,
    lookback: int,
    cap: float,
) -> dict[str, float] | None:
    """Inverse-volatility weights, rebalanced on the first bar of each month.

    ``closes`` holds history up to and including the current bar, with one
    column per ticker. Returns ``None`` when the last bar is not the first of a
    new calendar month. Returns ``{}`` when there are fewer than ``lookback + 1``
    rows or no ticker has a usable volatility. A ticker with zero or undefined
    volatility is omitted. Weights are inverse volatilities normalised to sum
    to one, then each clipped at ``cap`` with no redistribution.
    """
    if not period_rolled(closes.index, "M"):
        return None
    if len(closes) < lookback + 1:
        return {}
    vol = annualized_vol(closes, lookback)
    vol = vol[vol.notna() & (vol > 0.0)]
    if vol.empty:
        return {}
    inverse = 1.0 / vol
    weights = inverse / inverse.sum()
    return {str(t): min(float(w), cap) for t, w in weights.items()}


class QuantsmdMonthlyInverseVolBot(Bot):
    """
    Monthly inverse-volatility weighting on the eight core ETFs, capped at 35%.

    Args:
        lookback: Number of daily returns used for realised volatility.
        cap: Maximum weight for any single ETF. Excess stays in cash.

    The book is rebalanced on the first bar of each calendar month. Any other
    bar returns ``None`` and leaves the book untouched.
    """

    # A 63-bar volatility window needs more than the default 1y backtest window.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "lookback": [42, 63, 126],
        "cap": [0.30, 0.35, 0.40],
    }

    def __init__(
        self,
        lookback: int = LOOKBACK,
        cap: float = CAP,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdMonthlyInverseVolBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must exceed the volatility lookback, or no ETF is
            # evaluable and the bot holds cash without warning.
            period="2y",
            lookback=lookback,
            cap=cap,
            **kwargs,
        )
        self.lookback = lookback
        self.cap = cap

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float] | None:
        """Return the inverse-volatility book on month-first bars, else ``None``."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes, self.lookback, self.cap)


if __name__ == "__main__":
    run_bot(QuantsmdMonthlyInverseVolBot)
