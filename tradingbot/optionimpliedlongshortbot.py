"""OptionImpliedLongShortBot — Dollar-neutral long/short stock book from option-implied skew signals (Step E1).

Selects top-N stocks long (highest bullish option-implied sentiment / risk reversal)
and bottom-N stocks short (lowest sentiment / put skew), dollar-neutral (sum longs = 0.9,
sum shorts = -0.9, gross = 1.8 <= 1.8), with idle weight parked in SHV via park_idle_cash.

Universe: OPTION_CAPTURE_UNIVERSE single names (~50 liquid large caps) plus SHV for cash parking.
Schedule: Weekly (Friday near market close, e.g. 0 16 * * 5) or biweekly.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from typing import Any, ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.cash_park import park_idle_cash
from tradingbot.utils.implied_signal import (
    compute_cross_sectional_signals,
    load_signals_from_db,
)
from tradingbot.utils.runner import run_bot
from tradingbot.utils.universes import OPTION_CAPTURE_UNIVERSE

logger = logging.getLogger(__name__)

# Single-stock universe (exclude SPY and QQQ index underlyings)
DEFAULT_UNIVERSE: tuple[str, ...] = tuple(OPTION_CAPTURE_UNIVERSE[2:])
CASH_PROXY: str = "SHV"


@dataclass(frozen=True)
class ImpliedLongShortRules:
    """Rules and hyperparameters for OptionImpliedLongShortBot."""

    signal: str = "composite"  # 'rr25' or 'composite'
    smoothing: int = 5  # smoothing window in snapshots (days/weeks)
    top_n: int = 5  # number of names per side (top-N long, bottom-N short)
    rebalance: str = "weekly"  # 'weekly' or 'biweekly'
    gross_limit: float = 1.8  # maximum gross exposure (long + short <= 1.8)
    park_cash: bool = True  # park idle weight in CASH_PROXY (SHV)


RULES = ImpliedLongShortRules()


def compute_long_short_portfolio_weights(
    ranks: pd.Series,
    top_n: int = 5,
    gross_limit: float = 1.8,
) -> pd.Series:
    """Compute dollar-neutral long/short portfolio weights from cross-sectional ranks.

    Allocates (gross_limit / 2) / top_n to the top N names (highest rank, long),
    and -(gross_limit / 2) / top_n to the bottom N names (lowest rank, short).
    Names with missing (NaN) data receive 0.0 weight.
    """
    valid = ranks.dropna()
    weights = pd.Series(0.0, index=ranks.index, dtype=float)

    if len(valid) == 0 or top_n <= 0:
        return weights

    # If fewer names than 2 * top_n, cap side count so longs and shorts never overlap
    k = min(top_n, len(valid) // 2)
    if k == 0:
        return weights

    target_side = gross_limit / 2.0
    weight_per_name = target_side / float(top_n)

    sorted_valid = valid.sort_values(ascending=False)
    long_names = sorted_valid.iloc[:k].index
    short_names = sorted_valid.iloc[-k:].index

    weights.loc[long_names] = weight_per_name
    weights.loc[short_names] = -weight_per_name
    return weights


class OptionImpliedLongShortBot(Bot):
    """Dollar-neutral long/short equity bot ranked by option-implied skew."""

    RULES: ClassVar[ImpliedLongShortRules] = RULES
    ALLOW_SHORT: ClassVar[bool] = True
    MAX_GROSS: ClassVar[float] = 1.8
    CASH_PROXY: ClassVar[str] = CASH_PROXY
    SHORTABLE: ClassVar[tuple[str, ...]] = DEFAULT_UNIVERSE

    def __init__(
        self,
        rules: ImpliedLongShortRules | None = None,
        park_cash: bool = True,
        **kwargs: Any,
    ) -> None:
        self.rules = rules or self.RULES
        self.park_cash = park_cash
        self.tradeable_universe = list(DEFAULT_UNIVERSE)
        # Add SHV to tickers, but keep it excluded from self.tradeable_universe
        tickers = list(dict.fromkeys((*DEFAULT_UNIVERSE, self.CASH_PROXY)))
        super().__init__(
            "OptionImpliedLongShortBot",
            tickers=tickers,
            symbol=None,
            **kwargs,
        )

    def compute_target_weights(
        self,
        signals_df: pd.DataFrame | None = None,
        as_of: datetime.date | datetime.datetime | None = None,
    ) -> dict[str, float]:
        """Compute target long/short portfolio weights for the universe as of a date."""
        if signals_df is None or signals_df.empty:
            now = as_of or datetime.datetime.now(datetime.UTC)
            start_date = pd.to_datetime(now) - pd.Timedelta(days=self.rules.smoothing * 14 + 30)
            try:
                signals_df = load_signals_from_db(
                    self.tradeable_universe,
                    start=start_date,
                    end=pd.to_datetime(now) + pd.Timedelta(days=1),
                )
            except Exception:
                # Fail loudly: an unreadable DB must never read as "no signal" and sell the book into SHV.
                logger.exception("Failed to load option signals from DB")
                raise

        if signals_df is None or signals_df.empty:
            logger.warning("No option signals available for %s; holding the current book", self.tradeable_universe)
            return {}
        else:
            raw_weights: dict[str, float]
            ranked = compute_cross_sectional_signals(
                signals_df,
                signal_name=self.rules.signal,
                smoothing=self.rules.smoothing,
            )

            if ranked.empty:
                raw_weights = {}
            else:
                if as_of is not None:
                    as_of_ts = pd.to_datetime(as_of)
                    ranked = ranked[ranked["snapshot_at"] <= as_of_ts]

                if ranked.empty:
                    raw_weights = {}
                else:
                    latest_time = ranked["snapshot_at"].max()
                    latest_rows = ranked[ranked["snapshot_at"] == latest_time].set_index("underlying")
                    ranks_series = latest_rows["rank"]
                    weights_series = compute_long_short_portfolio_weights(
                        ranks_series,
                        top_n=self.rules.top_n,
                        gross_limit=self.rules.gross_limit,
                    )
                    raw_weights = {str(k): float(v) for k, v in weights_series.items() if abs(v) > 1e-6}

        # Idle weight parked in SHV (only when there is a book to complement)
        if self.park_cash and raw_weights:
            raw_weights = park_idle_cash(
                raw_weights,
                proxy=self.CASH_PROXY,
                max_gross=self.MAX_GROSS,
            )

        return raw_weights

    def targetWeights(self, rows: dict[str, pd.Series] | None = None) -> dict[str, float] | None:
        """Framework targetWeights interface for backtests and cron runs."""
        as_of = None
        if rows:
            try:
                as_of = max(getattr(r, "name", None) for r in rows.values() if getattr(r, "name", None) is not None)
            except Exception:
                as_of = None
        return self.compute_target_weights(as_of=as_of)

    def makeOneIteration(self) -> int:
        """Fetch latest option signals, select top/bottom N, and rebalance."""
        weights = self.compute_target_weights()
        if not weights:
            logger.info("OptionImpliedLongShortBot: no active positions or idle weight to rebalance")
            return 0

        logger.info("OptionImpliedLongShortBot target weights: %s", weights)
        self.rebalancePortfolio(weights, onlyOver50USD=True)
        return 0


if __name__ == "__main__":
    run_bot(OptionImpliedLongShortBot)
