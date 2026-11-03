"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-weights-20-risk-parity-capped
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_20_risk_parity_capped.py (Apache-2.0, (c) 2026 QuantJourney)

Monthly risk parity across the nine S&P sector ETFs. Weights equalise each
sector's contribution to portfolio volatility, estimated from the trailing
126 daily returns, then each weight is capped at 25% with the excess
re-spread over the uncapped names. Rebalances on the first bar of a month.

Departures from source:
- The source's RiskParityModel is not available in the upstream repo, so it is
  re-implemented here as equal-risk-contribution (ERC) with damped fixed-point
  iteration. The 25% cap is applied after ERC, as in the source chain.
- Weights are long-only and gross <= 1.0, as the framework requires. The source
  is already long-only, so nothing else changes.
"""

from typing import ClassVar

import numpy as np
import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import SECTORS9, closes_frame, period_rolled
from tradingbot.utils.runner import run_bot

UNIVERSE = list(SECTORS9)
LOOKBACK = 126
CAP = 0.25


def _erc(cov: np.ndarray, iters: int = 500) -> np.ndarray:
    """Equal-risk-contribution weights for covariance matrix ``cov``.

    Starts from equal weights and repeatedly moves each weight toward an equal
    share of total risk, averaging old and new weights for damping. Returns
    all-equal weights if the total risk is not positive.
    """
    n = cov.shape[0]
    w = np.full(n, 1.0 / n)
    for _ in range(iters):
        rc = w * (cov @ w)  # risk contributions
        total = rc.sum()
        if total <= 0:
            break
        w_new = w * (total / n) / np.where(rc > 0, rc, np.nan)
        w_new = np.nan_to_num(w_new, nan=0.0)
        w_new = w_new / w_new.sum()
        w = 0.5 * w + 0.5 * w_new
    return w


def _cap(w: np.ndarray, cap: float) -> np.ndarray:
    """Clip weights at ``cap`` and re-spread the excess over uncapped names.

    The re-spread is proportional to current weights. Repeats up to 20 times
    so that names pushed over the cap by the re-spread are clipped too.
    """
    for _ in range(20):
        over = w > cap + 1e-12
        if not over.any():
            break
        excess = (w[over] - cap).sum()
        w = np.where(over, cap, w)
        free = ~over & (w < cap - 1e-12)
        if not free.any():
            break
        w[free] += excess * w[free] / w[free].sum()
    return w


def compute_weights(
    closes: pd.DataFrame,
    lookback: int,
    cap: float,
) -> dict[str, float] | None:
    """Capped equal-risk-contribution weights, rebalanced monthly.

    ``closes`` holds history up to and including the current bar, with one
    column per ticker. Returns ``None`` unless the last bar is the first bar
    of a calendar month. Fewer than ``lookback + 1`` rows returns ``{}``.

    The covariance comes from the last ``lookback`` daily returns, with
    tickers that have any missing return in that window dropped. Weights
    below 1e-9 are omitted, so the book is long-only and sums to at most 1.0.
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
    w = _cap(_erc(cov), cap)
    out: dict[str, float] = {}
    for ticker, weight in zip(returns.columns, w, strict=True):
        if weight >= 1e-9:
            out[str(ticker)] = float(weight)
    return out


class QuantsmdRiskParityCappedBot(Bot):
    """
    Monthly equal-risk-contribution weights on the nine S&P sector ETFs, capped at 25%.

    Args:
        lookback: Trailing daily returns used for the covariance estimate.
            Also the warmup length before the first book is built.
        cap: Maximum weight for any single sector.

    The book is rebuilt on the first bar of each calendar month only. Off-month
    bars return ``None`` so the book is left unchanged.
    """

    # A 126-bar covariance window needs more than the default 1y backtest.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "lookback": [63, 126, 252],
        "cap": [0.20, 0.25, 0.30],
    }

    def __init__(
        self,
        lookback: int = LOOKBACK,
        cap: float = CAP,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdRiskParityCappedBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must exceed the covariance lookback, or no book is ever built.
            period="3y",
            lookback=lookback,
            cap=cap,
            **kwargs,
        )
        self.lookback = lookback
        self.cap = cap

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float] | None:
        """Return the risk-parity book on month-first bars, ``None`` otherwise."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes, self.lookback, self.cap)


if __name__ == "__main__":
    run_bot(QuantsmdRiskParityCappedBot)
