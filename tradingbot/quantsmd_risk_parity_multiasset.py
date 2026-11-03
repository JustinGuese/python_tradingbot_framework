"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-weights-19-risk-parity-multiasset
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_19_risk_parity_multiasset.py (Apache-2.0, (c) 2026 QuantJourney)

Monthly risk parity across eight multi-asset ETFs (equity, international equity,
emerging equity, long and intermediate Treasuries, gold, commodities and REITs).
Weights equalise each asset's contribution to portfolio volatility, estimated
from the trailing 63 daily returns. Rebalances on the first bar of a month.

Departures from source:
- The source's RiskParityModel is not available in the upstream repo, so it is
  re-implemented as equal-risk-contribution (ERC) with the same damped
  fixed-point iteration used by quantsmd_risk_parity_capped.
- No per-asset cap is applied, so the ERC weights are the book.
- Weights are long-only and gross <= 1.0, as the framework requires. ERC
  weights are already non-negative, so nothing else changes.
"""

from typing import ClassVar

import numpy as np
import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF8, closes_frame, erc_weights, period_rolled
from tradingbot.utils.runner import run_bot

UNIVERSE = list(ETF8)
LOOKBACK = 63


def compute_weights(
    closes: pd.DataFrame,
    lookback: int,
) -> dict[str, float] | None:
    """Equal-risk-contribution weights, rebalanced monthly.

    ``closes`` holds history up to and including the current bar, with one
    column per ticker. Returns ``None`` unless the last bar is the first bar
    of a calendar month. Fewer than ``lookback + 1`` rows returns ``{}``.

    The covariance comes from the last ``lookback`` daily returns, with
    tickers that have any missing return in that window dropped. Returns
    ``{}`` if no ticker is left, or if the covariance is not finite or has
    non-positive trace. Weights below 1e-9 are omitted. There is no cap.
    """
    if not period_rolled(closes.index, "M"):
        return None
    if len(closes) < lookback + 1:
        return {}
    returns = closes.pct_change().iloc[-lookback:].dropna(axis=1, how="any")
    if returns.shape[1] == 0:
        return {}
    cov = returns.cov().to_numpy(dtype=float)
    if not np.all(np.isfinite(cov)) or np.trace(cov) <= 0:
        return {}
    w = erc_weights(cov)
    out: dict[str, float] = {}
    for ticker, weight in zip(returns.columns, w, strict=True):
        if weight >= 1e-9:
            out[str(ticker)] = float(weight)
    return out


class QuantsmdRiskParityMultiassetBot(Bot):
    """
    Monthly equal-risk-contribution weights on eight multi-asset ETFs, no cap.

    Args:
        lookback: Trailing daily returns used for the covariance estimate.
            Also the warmup length before the first book is built.

    The book is rebuilt on the first bar of each calendar month only. Off-month
    bars return ``None`` so the book is left unchanged.
    """

    # A 63-bar covariance window is short, but the max period keeps the backtest
    # on the same long history as the other daily bots.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "lookback": [63, 126],
    }

    def __init__(
        self,
        lookback: int = LOOKBACK,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdRiskParityMultiassetBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must exceed the covariance lookback, or no book is ever built.
            period="2y",
            lookback=lookback,
            **kwargs,
        )
        self.lookback = lookback

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float] | None:
        """Return the risk-parity book on month-first bars, ``None`` otherwise."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes, self.lookback)


if __name__ == "__main__":
    run_bot(QuantsmdRiskParityMultiassetBot)
