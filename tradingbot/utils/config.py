"""
Configuration, constants, and global setup for the trading bot system.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass

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

# Execution cost model — these MUST mirror backtest_bot()'s defaults in
# backtest.py, or the live equity curve stops being comparable to the
# backtested one (which is the whole reason the model exists).
DEFAULT_SLIPPAGE_PCT = 0.0005  # 5 bps, one way
DEFAULT_COMMISSION_PCT = 0.0  # fraction of trade value

# No-trade band. Calibrated on production data: RegimeAdaptiveBot and
# EarningsInsiderTiltBot were putting 93% / 92% of their trades through at under
# $25 notional (smallest: $0.011), because the old band was a flat $1 regardless
# of book or position size.
DEFAULT_MIN_TRADE_USD = 25.0
DEFAULT_REBALANCE_BAND_PCT = 0.05

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
    "GLD",
    "AAPL",
    "MSFT",
    "GOOG",
    "TSLA",
    "AMD",
    "AMZN",
    "DG",
    "KDP",
    "LLY",
    "NOC",
    "NVDA",
    "PGR",
    "TEAM",
    "UNH",
    "WM",
    "URTH",
    "IWDA.AS",
    "EEM",
    "XAIX.DE",
    "BTEC.L",
    "L0CK.DE",
    "2B76.DE",
    "W1TA.DE",
    "RENW.DE",
    "BNXG.DE",
    "BTC-USD",
    "ETH-USD",
    "AVAX-USD",
    "TMF",
    "FAS",
    "TQQQ",
    "QQQ",
    "UUP",
    "META",
    "PYPL",
    "ADBE",
    "UPRO",
    "BSV",
    "SQQQ",
    "NTSX",
    "DBMF",
    "VDE",
    "VNQ",
    "VHT",
    "VFH",
    "VOX",
    "VPU",
    "VAW",
    "VGT",
    "VIS",
    "VDC",
    "VCR",
    "VLUE",
    "FNDX",
    "VTV",
    "RWL",
    "DBA",
    "SHV",
    "DBB",
    "DBO",
    "URA",
    "WOOD",
    "DBE",
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


def _env_float(name: str, default: float, *, lo: float, hi: float) -> float:
    """
    Read a float from the environment, falling back to `default`.

    Deliberately never raises. This module is imported at process start by all
    28 bot CronJobs, so an unparseable or out-of-range value must degrade to the
    default rather than take the entire fleet down at once.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    log = logging.getLogger(__name__)
    try:
        value = float(raw)
    except ValueError:
        log.warning("Invalid %s=%r; using default %s", name, raw, default)
        return default
    if not lo <= value <= hi:
        log.warning("%s=%s outside [%s, %s]; using default %s", name, value, lo, hi, default)
        return default
    return value


@dataclass(frozen=True)
class ExecutionConfig:
    """
    Transaction costs and the no-trade band for LIVE execution.

    Cost convention, mirroring backtest.py exactly:
      BUY  — `quantity_usd` is the GROSS cash budget. Commission comes out of
             that budget, never on top of it; slippage raises the execution
             price. Cash is debited by the full gross budget, which is what
             makes "spend all cash" incapable of overdrawing at any commission.
      SELL — slippage lowers the execution price; commission is taken from the
             post-slippage proceeds; USD is credited with the net.

    Invariant: min_trade_usd <= PORTFOLIO_CONFIG.min_asset_value_usd. If the
    trade floor exceeded the position floor, rebalance_portfolio could approve a
    target position it is then forbidden to open.

    Rollback without a code change or redeploy — set all four to
    0 / 0 / 1 / 0 and the pre-cost-model behaviour is reproduced.

    If EXECUTION_COMMISSION_PCT is ever set non-zero, add a `fees` column to
    Trade: buy-side commission is the one quantity not otherwise recoverable
    (on sells it is `quantity * price - profit`).
    """

    slippage_pct: float = DEFAULT_SLIPPAGE_PCT
    commission_pct: float = DEFAULT_COMMISSION_PCT
    min_trade_usd: float = DEFAULT_MIN_TRADE_USD
    rebalance_band_pct: float = DEFAULT_REBALANCE_BAND_PCT

    @classmethod
    def from_env(cls) -> ExecutionConfig:
        return cls(
            slippage_pct=_env_float("EXECUTION_SLIPPAGE_PCT", DEFAULT_SLIPPAGE_PCT, lo=0.0, hi=0.05),
            commission_pct=_env_float("EXECUTION_COMMISSION_PCT", DEFAULT_COMMISSION_PCT, lo=0.0, hi=0.05),
            min_trade_usd=_env_float("EXECUTION_MIN_TRADE_USD", DEFAULT_MIN_TRADE_USD, lo=0.0, hi=10_000.0),
            rebalance_band_pct=_env_float("EXECUTION_REBALANCE_BAND_PCT", DEFAULT_REBALANCE_BAND_PCT, lo=0.0, hi=1.0),
        )

    def buy_execution_price(self, price: float) -> float:
        return price * (1.0 + self.slippage_pct)

    def sell_execution_price(self, price: float) -> float:
        return price * (1.0 - self.slippage_pct)

    def no_trade_threshold(self, reference_usd: float) -> float:
        """
        USD size below which a rebalancing adjustment is not worth executing.

        `reference_usd` should be max(target_value, current_value): the band
        scales with position size so it does not need retuning as a book grows.
        """
        return max(self.min_trade_usd, self.rebalance_band_pct * abs(reference_usd))


DATA_CONFIG = DataConfig()
PORTFOLIO_CONFIG = PortfolioConfig()
EXECUTION_CONFIG = ExecutionConfig.from_env()


# ------------------------------------------------------------------
# Logging Setup
# ------------------------------------------------------------------


def setup_logging(level: int = logging.INFO, log_file: str | None = None, include_timestamp: bool = True) -> None:
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
