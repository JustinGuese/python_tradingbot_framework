"""
Regime-adaptive bot: classify regime from VIX, index trend, Fear & Greed; apply regime tilt; rebalance.

All regime and tilt logic lives in `utils.portfolio`; this bot only fetches data and orchestrates.
"""

from dataclasses import dataclass
from typing import List

from utils.core import Bot
from utils.portfolio import (
    TRADEABLE,
    get_fear_greed_index,
    index_close_series_from_wide,
    regime_compute_weights,
    vix_series_from_long_df,
)


@dataclass(frozen=True)
class RegimeConfig:
    """Configuration for the regime-adaptive strategy."""

    universe: List[str]
    index_symbol: str = "QQQ"
    interval: str = "1d"
    lookback_period: str = "60d"
    vix_symbol: str = "^VIX"
    vix_interval: str = "1d"
    vix_period: str = "14d"


class RegimeAdaptiveBot(Bot):
    """
    Bot that rebalances using regime classification (defensive / momentum / mean_reversion)
    and regime-specific weight tilts.
    """

    def __init__(self):
        super().__init__("RegimeAdaptiveBot", symbol=None)
        # Strategy configuration (universe, index, lookbacks) is centralized here
        self.config = RegimeConfig(universe=list(TRADEABLE))
        self.tradeable_symbols = self.config.universe

    def makeOneIteration(self):
        """
        Fetch VIX, QQQ + universe, Fear & Greed; classify regime; apply tilt; rebalance.
        Returns 0.
        """
        cfg = self.config
        syms = self.tradeable_symbols
        n = len(syms)
        if n == 0:
            return 0

        # 1. VIX (long-format data; helper extracts series)
        vix_data_long = None
        try:
            vix_data_long = self.getYFData(
                cfg.vix_symbol,
                interval=cfg.vix_interval,
                period=cfg.vix_period,
            )
        except Exception:
            vix_data_long = None
        vix_series = vix_series_from_long_df(vix_data_long) if vix_data_long is not None else None

        # 2. Index (e.g. QQQ) + universe; wide close df (helper extracts index series)
        data_long = self.getYFDataMultiple(
            [cfg.index_symbol] + list(syms),
            interval=cfg.interval,
            period=cfg.lookback_period,
            saveToDB=True,
        )
        wide_close_df = self.convertToWideFormat(
            data_long, value_column="close", fill_method="both"
        )
        qqq_close_series = index_close_series_from_wide(
            wide_close_df, index_symbol=cfg.index_symbol
        )

        # 3. Fear & Greed
        fg_value = get_fear_greed_index()

        # 4. Compute regime-tilted weights via pure helper
        weights = regime_compute_weights(
            symbols=syms,
            vix_series_or_value=vix_series,
            index_close_series=qqq_close_series,
            wide_close_df=wide_close_df,
            fear_greed_value=fg_value,
        )
        self.rebalancePortfolio(weights, onlyOver50USD=True)
        return 0

if __name__ == "__main__":
    bot = RegimeAdaptiveBot()
    # bot.local_backtest() # not possible, event driven
    bot.run()