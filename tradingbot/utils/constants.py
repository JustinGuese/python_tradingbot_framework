"""
Constants used across the trading bot system.

This module defines system-wide constants for data freshness, caching,
portfolio management, and data validation.
"""

# Data freshness tolerance in minutes
# Data older than this is considered stale and will be refetched from yfinance
FRESHNESS_TOLERANCE_MINUTES = 10

# Price cache settings
# TTL cache for getLatestPrice() to avoid redundant database queries
PRICE_CACHE_MAXSIZE = 128  # Maximum number of symbols to cache
PRICE_CACHE_TTL = 60  # Cache time-to-live in seconds

# Minimum asset value for portfolio rebalancing (USD)
# Assets with target value below this threshold can be filtered out during rebalancing
MIN_ASSET_VALUE_USD = 50.0

# Required DataFrame columns for market data
# All market data DataFrames must have these columns in this exact order
REQUIRED_DATA_COLUMNS = [
    "symbol",
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
]

