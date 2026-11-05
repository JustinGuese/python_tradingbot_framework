"""
Static blend of the four positive-alpha quants.md bots.

Combines the four strategies showing positive alpha in walk-forward evaluation:
  1. QuantsmdSmaDailyGatedBot (weight 0.4): Daily sector SMA trend on the nine
     S&P sectors, gated by SPY's 200-bar SMA.
  2. QuantsmdCrossSectionalReversalV2Bot (weight 0.2): Weekly sector reversal with
     a rank buffer, rebalanced on Fridays.
  3. QuantsmdCrossSectionalMomentumV2Bot (weight 0.2): Monthly sector momentum
     skipping the latest month with a volatility filter, rebalanced on month-first bars.
  4. QuantsmdQuarterlyTeCostGateGatedBot (weight 0.2): Quarterly momentum rotation
     on ETF8 with tracking-error early exit and turnover gate, gated by SPY's 200-bar SMA.

Weights are equal-ish static priors (largest on the one candidate that cleared
t >= 2). There is no fitting across components, so there is nothing to overfit.

Stateless non-rebalance handling:
  * Components that rebalance periodically (reversal off-Friday, momentum off-month-first,
    quarterly TE gate off-quarter) return None when no rebalance is due.
  * In a stateful live or backtest environment, an untouched component would hold its
    last target book. Because compute_weights is a pure, stateless function, the last
    target book cannot be known here. Therefore, None is treated as an empty book ({})
    for that component on that bar.
  * Every component call is wrapped in a try/except block; a component raising an
    exception contributes an empty book ({}) and does not take down the others.

The final blended book is the weighted sum of component books per ticker, scaled
via scale_to_max_gross(blend, 1.0). Idle capital remains in cash (no SHV parking).
"""

from __future__ import annotations

import logging
from typing import ClassVar

import pandas as pd

from tradingbot import (
    quantsmd_cross_sectional_momentum_v2 as momentum_v2,
)
from tradingbot import (
    quantsmd_cross_sectional_reversal_v2 as reversal_v2,
)
from tradingbot import (
    quantsmd_quarterly_te_cost_gate_gated as te_gated,
)
from tradingbot import (
    quantsmd_sma_daily_gated as sma_gated,
)
from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF8, SECTORS9, closes_frame, scale_to_max_gross
from tradingbot.utils.runner import run_bot

logger = logging.getLogger(__name__)

UNIVERSE = list(dict.fromkeys([*SECTORS9, *ETF8, "SPY"]))

COMPONENT_WEIGHTS: dict[str, float] = {
    "sma_gated": 0.4,
    "reversal": 0.2,
    "momentum": 0.2,
    "te_gated": 0.2,
}


def compute_weights(
    closes: pd.DataFrame,
    weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """Blend the four positive-alpha quants.md bots by component weights.

    Builds each component's target book from its relevant subset of ``closes``
    (sectors for reversal and momentum, sectors + SPY for sma_gated, and ETF8
    for te_gated).

    A component returning ``None`` (such as off-rebalance bars for weekly or
    monthly strategies) is treated as ``{}``, because the component's last target
    book is not knowable statelessly in this function. Any component raising an
    exception is logged and replaced with ``{}``.

    The resulting books are multiplied by their respective component weights,
    summed per ticker, and scaled via ``scale_to_max_gross(blend, 1.0)`` so gross
    exposure never exceeds 1.0.
    """
    if closes.empty:
        return {}

    comp_weights = COMPONENT_WEIGHTS if weights is None else weights
    blend: dict[str, float] = {}

    for name, comp_weight in comp_weights.items():
        if comp_weight <= 0:
            continue

        book: dict[str, float] | None = None
        try:
            if name == "sma_gated":
                cols = [c for c in (*SECTORS9, "SPY") if c in closes.columns]
                sub = closes[cols] if cols else pd.DataFrame(index=closes.index)
                book = sma_gated.compute_weights(
                    sub,
                    sma_gated.FAST,
                    sma_gated.SLOW,
                    sma_gated.CAP,
                    sma_gated.OFF_SCALE,
                )
            elif name == "reversal":
                cols = [c for c in SECTORS9 if c in closes.columns]
                sub = closes[cols] if cols else pd.DataFrame(index=closes.index)
                book = reversal_v2.compute_weights(
                    sub,
                    reversal_v2.LOOKBACK,
                    reversal_v2.N_LONG,
                    reversal_v2.BUFFER,
                )
            elif name == "momentum":
                cols = [c for c in SECTORS9 if c in closes.columns]
                sub = closes[cols] if cols else pd.DataFrame(index=closes.index)
                book = momentum_v2.compute_weights(
                    sub,
                    momentum_v2.LOOKBACK,
                    momentum_v2.N_LONG,
                    momentum_v2.SKIP,
                )
            elif name == "te_gated":
                cols = [c for c in ETF8 if c in closes.columns]
                sub = closes[cols] if cols else pd.DataFrame(index=closes.index)
                state = {"last_target": None, "turnover": []}
                book = te_gated.compute_weights(
                    sub,
                    te_gated.LOOKBACK,
                    te_gated.TOP_N,
                    te_gated.CAP,
                    te_gated.TE_THRESHOLD,
                    te_gated.TE_WINDOW,
                    te_gated.MAX_ANNUAL_TURNOVER,
                    state,
                    te_gated.OFF_SCALE,
                )
            else:
                logger.warning("Unknown blend component %s; skipping", name)
                continue
        except Exception:
            logger.exception("Component %s failed; using empty book", name)
            book = {}

        if book is None:
            book = {}

        for ticker, w in book.items():
            blend[ticker] = blend.get(ticker, 0.0) + float(w) * float(comp_weight)

    return scale_to_max_gross(blend, 1.0)


class QuantsmdGatedBlendBot(Bot):
    """
    Static blend of four positive-alpha quants.md bots:
    QuantsmdSmaDailyGatedBot (40%), QuantsmdCrossSectionalReversalV2Bot (20%),
    QuantsmdCrossSectionalMomentumV2Bot (20%), and
    QuantsmdQuarterlyTeCostGateGatedBot (20%).
    """

    BACKTEST_PERIOD: ClassVar[str | None] = "max"
    param_grid: ClassVar[dict] = {}

    def __init__(
        self,
        weights: dict[str, float] | None = None,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdGatedBlendBot",
            tickers=list(UNIVERSE),
            benchmark_tickers=[],
            interval="1d",
            period="2y",
            **kwargs,
        )
        self.weights = weights

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the blended target book for the current bar."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes, self.weights)


if __name__ == "__main__":
    run_bot(QuantsmdGatedBlendBot)
