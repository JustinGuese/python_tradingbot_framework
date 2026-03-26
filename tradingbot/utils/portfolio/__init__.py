"""
Portfolio and strategy utilities for trading bots.

This subpackage groups helpers that define portfolio construction,
regime logic, and portfolio-worth analytics, independent of any
particular bot implementation.

The actual implementation modules still live at the top level of
`tradingbot.utils` to preserve existing import paths; this package
provides a clearer, domain-oriented API surface under
`utils.portfolio.*`.
"""

from ..earnings_insider import (
    earnings_insider_compute_weights,
    score_symbols_earnings_insider,
    tilt_weights_by_scores,
)
from ..portfolio_utils import (
    calculate_performance_metrics,
    calculate_portfolio_worth,
    get_portfolio_worth_history,
    sharpe_compute_weights,
    get_fear_greed_index,
)
from ..config import TRADEABLE
from ..regime import (
    vix_series_from_long_df,
    index_close_series_from_wide,
    classify_regime,
    apply_regime_tilt,
    regime_compute_weights,
)

__all__ = [
    # Symbol universe
    "TRADEABLE",
    # Regime logic
    "vix_series_from_long_df",
    "index_close_series_from_wide",
    "classify_regime",
    "apply_regime_tilt",
    "regime_compute_weights",
    # Earnings / insider tilting
    "score_symbols_earnings_insider",
    "tilt_weights_by_scores",
    "earnings_insider_compute_weights",
    # Sharpe / optimisation helpers
    "sharpe_compute_weights",
    # Portfolio worth + analytics
    "calculate_portfolio_worth",
    "get_portfolio_worth_history",
    "calculate_performance_metrics",
    # Sentiment adapter
    "get_fear_greed_index",
]

