"""
Configuration, constants, and global setup for the trading bot system.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Optional

# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------

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

# Canonical tradeable symbol universe shared by Sharpe, earnings-insider, regime, and AI tool bots.
TRADEABLE = [
    "GLD", "AAPL", "MSFT", "GOOG", "TSLA", "AMD", "AMZN", "DG", "KDP", "LLY",
    "NOC", "NVDA", "PGR", "TEAM", "UNH", "WM", "URTH", "IWDA.AS", "EEM",
    "XAIX.DE", "BTEC.L", "L0CK.DE", "2B76.DE", "W1TA.DE", "RENW.DE", "BNXG.DE",
    "BTC-USD", "ETH-USD", "AVAX-USD", "TMF", "FAS", "TQQQ", "QQQ", "UUP",
    "META", "PYPL", "ADBE", "UPRO", "BSV", "SQQQ", "NTSX", "DBMF", "VDE", "VNQ",
    "VHT", "VFH", "VOX", "VPU", "VAW", "VGT", "VIS", "VDC", "VCR", "VLUE",
    "FNDX", "VTV", "RWL", "DBA", "SHV", "DBB", "DBO", "URA", "WOOD", "DBE",
]

# ------------------------------------------------------------------
# Configuration Objects
# ------------------------------------------------------------------

@dataclass(frozen=True)
class DataConfig:
    """Configuration for data freshness and caching."""
    freshness_tolerance_minutes: int = FRESHNESS_TOLERANCE_MINUTES
    price_cache_maxsize: int = PRICE_CACHE_MAXSIZE
    price_cache_ttl: int = PRICE_CACHE_TTL


@dataclass(frozen=True)
class PortfolioConfig:
    """Configuration for portfolio management thresholds."""
    min_asset_value_usd: float = MIN_ASSET_VALUE_USD


DATA_CONFIG = DataConfig()
PORTFOLIO_CONFIG = PortfolioConfig()


# ------------------------------------------------------------------
# Logging Setup
# ------------------------------------------------------------------

def setup_logging(
    level: int = logging.INFO,
    log_file: Optional[str] = None,
    include_timestamp: bool = True
) -> None:
    """
    Setup centralized logging for the trading bot framework.
    
    Args:
        level: Logging level (default: logging.INFO)
        log_file: Optional path to a log file
        include_timestamp: Whether to include timestamps in the logs
    """
    # Create format string
    if include_timestamp:
        fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        datefmt = "%Y-%m-%d %H:%M:%S"
    else:
        fmt = "%(name)s - %(levelname)s - %(message)s"
        datefmt = None

    # Configure root logger
    root_logger = logging.getLogger()
    
    # Avoid duplicate handlers if setup_logging is called multiple times
    if root_logger.hasHandlers():
        root_logger.handlers.clear()
        
    root_logger.setLevel(level)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    if datefmt:
        console_handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    else:
        console_handler.setFormatter(logging.Formatter(fmt))
    root_logger.addHandler(console_handler)

    # File handler (if requested)
    if log_file:
        file_handler = logging.FileHandler(log_file)
        if datefmt:
            file_handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
        else:
            file_handler.setFormatter(logging.Formatter(fmt))
        root_logger.addHandler(file_handler)

    # Set external libraries to higher levels to reduce noise
    logging.getLogger("yfinance").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
    logging.getLogger("peewee").setLevel(logging.WARNING)
