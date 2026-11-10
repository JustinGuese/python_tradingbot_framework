"""option_ImpliedSignalBot — stock selection from option-implied signals.

Selects and holds an equal-weighted basket of the top-N single stocks ranked
by option-implied signals (Cremers-Weinbaum call-put spread, put skew, risk
reversal 25, or composite), remainder in cash.

Schedule: 0 16 * * 5 (weekly on Friday near market close).
"""

from __future__ import annotations

from dataclasses import dataclass
import datetime
import logging
from typing import ClassVar, Sequence

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.implied_signal import (
    compute_cross_sectional_signals,
    compute_portfolio_weights,
    load_signals_from_db,
)
from tradingbot.utils.runner import run_bot
from tradingbot.utils.universes import OPTION_CAPTURE_UNIVERSE

logger = logging.getLogger(__name__)

# Single-stock universe (exclude SPY and QQQ index underlyings)
DEFAULT_UNIVERSE = list(OPTION_CAPTURE_UNIVERSE[2:])


@dataclass(frozen=True)
class ImpliedSignalRules:
    """Rules and hyperparameters for OptionImpliedSignalBot."""

    signal: str = "composite"  # cp_spread, put_skew, rr25, composite
    smoothing: int = 5  # smoothing window in snapshots (days/weeks)
    top_n: int = 5  # number of top names to hold
    rebalance: str = "weekly"  # weekly or biweekly


RULES = ImpliedSignalRules()


class OptionImpliedSignalBot(Bot):
    """Equity bot holding the top-N stocks by option-implied sentiment."""

    RULES: ClassVar[ImpliedSignalRules] = RULES

    def __init__(self, rules: ImpliedSignalRules | None = None, **kwargs):
        super().__init__("option_ImpliedSignalBot", symbol=None, **kwargs)
        self.rules = rules or self.RULES
        self.tradeable_universe = list(DEFAULT_UNIVERSE)

    def compute_target_weights(
        self,
        signals_df: pd.DataFrame | None = None,
        as_of: datetime.date | datetime.datetime | None = None,
    ) -> dict[str, float]:
        """Compute target portfolio weights for the universe as of a date."""
        if signals_df is None or signals_df.empty:
            now = as_of or datetime.datetime.now(datetime.timezone.utc)
            start_date = pd.to_datetime(now) - pd.Timedelta(days=self.rules.smoothing * 14 + 30)
            try:
                signals_df = load_signals_from_db(
                    self.tradeable_universe,
                    start=start_date,
                    end=pd.to_datetime(now) + pd.Timedelta(days=1),
                )
            except Exception as e:
                logger.error("Failed to load option signals from DB: %s", e)
                return {}

        if signals_df.empty:
            logger.warning("No option signals available for %s", self.tradeable_universe)
            return {}

        ranked = compute_cross_sectional_signals(
            signals_df,
            signal_name=self.rules.signal,
            smoothing=self.rules.smoothing,
        )

        if ranked.empty:
            return {}

        # Latest snapshot
        latest_time = ranked["snapshot_at"].max()
        latest_rows = ranked[ranked["snapshot_at"] == latest_time].set_index("underlying")

        ranks_series = latest_rows["rank"]
        weights_series = compute_portfolio_weights(ranks_series, top_n=self.rules.top_n)

        return {str(k): float(v) for k, v in weights_series.items() if v > 0}

    def makeOneIteration(self) -> int:
        """Fetch latest option signals, select top-N names, and rebalance."""
        weights = self.compute_target_weights()
        if not weights:
            logger.info("OptionImpliedSignalBot: no active positions to rebalance")
            return 0

        logger.info("OptionImpliedSignalBot target weights: %s", weights)
        self.rebalancePortfolio(weights, onlyOver50USD=True)
        return 0


if __name__ == "__main__":
    run_bot(OptionImpliedSignalBot)
