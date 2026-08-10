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

from ..config import TRADEABLE
from ..earnings_insider import (
    earnings_insider_compute_weights,
    score_symbols_earnings_insider,
    tilt_weights_by_scores,
)
from ..portfolio_utils import (
    calculate_performance_metrics,
    calculate_portfolio_worth,
    get_fear_greed_index,
    get_portfolio_worth_history,
    sharpe_compute_weights,
)
from ..regime import (
    apply_regime_tilt,
    classify_regime,
    index_close_series_from_wide,
    regime_compute_weights,
    vix_series_from_long_df,
)

__all__ = [
    "TRADEABLE",
    "apply_regime_tilt",
    "calculate_performance_metrics",
    "calculate_portfolio_worth",
    "classify_regime",
    "earnings_insider_compute_weights",
    "get_fear_greed_index",
    "get_portfolio_worth_history",
    "index_close_series_from_wide",
    "regime_compute_weights",
    "score_symbols_earnings_insider",
    "sharpe_compute_weights",
    "tilt_weights_by_scores",
    "vix_series_from_long_df",
]
