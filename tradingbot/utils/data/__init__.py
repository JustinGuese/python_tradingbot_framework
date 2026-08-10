"""
Data-access utilities for trading bots.

This subpackage groups helpers that interact with external data sources
or transform raw market/fundamental data into tabular form.

Current contents are thin re-exports from the historical flat `utils`
module layout to avoid a disruptive move:
- `DataService`: unified Yahoo Finance + DB data access
- `load_stock_news_earnings_insider`, `get_portfolio_symbols`: yfinance
  loaders for news, earnings, and insider trades
"""

from ..data_service import DataService
from ..stock_fundamentals_loader import (
    EARNINGS_LIMIT,
    NEWS_COUNT,
    SYMBOL_DELAY_SECONDS,
    get_portfolio_symbols,
    load_stock_news_earnings_insider,
)

__all__ = [
    "EARNINGS_LIMIT",
    "NEWS_COUNT",
    "SYMBOL_DELAY_SECONDS",
    "DataService",
    "get_portfolio_symbols",
    "load_stock_news_earnings_insider",
]
