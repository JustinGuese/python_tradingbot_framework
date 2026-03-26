"""
Core infrastructure utilities for trading bots.

This subpackage groups together:
- Bot orchestration and lifecycle (`botclass`, `bot_repository`, `portfolio_manager`)
- Database models and sessions (`db`)
- Generic infrastructure helpers (`constants`, `helpers`)
- Backtesting and tuning utilities (`backtest`, `hyperparameter_tuning`)

Implementation note:
- The actual implementation modules still live at the top level of `tradingbot.utils`
  to keep the diff small and preserve historical paths.
- This package simply re-exports a curated, stable core API under
  `utils.core.*` so new code can depend on clearer domain boundaries.
"""

from ..botclass import Bot
from ..bot_repository import BotRepository
from ..config import (
    FRESHNESS_TOLERANCE_MINUTES,
    MIN_ASSET_VALUE_USD,
    PRICE_CACHE_MAXSIZE,
    PRICE_CACHE_TTL,
    REQUIRED_DATA_COLUMNS,
    setup_logging,
)
from ..helpers import (
    ensure_utc_series,
    ensure_utc_timestamp,
    parse_period_to_date_range,
    validate_dataframe_columns,
)
from ..portfolio_manager import PortfolioManager
from ..backtest import backtest_bot, _get_backtest_period
from ..hyperparameter_tuning import (
    get_default_param_grid,
    tune_hyperparameters,
)

__all__ = [
    # Core bot + repo
    "Bot",
    "BotRepository",
    "PortfolioManager",
    # DB access
    "Base",
    "BotModel",
    "DATABASE_URL",
    "HistoricData",
    "PortfolioWorth",
    "RunLog",
    "SessionLocal",
    "StockEarnings",
    "StockInsiderTrade",
    "StockNews",
    "Trade",
    "engine",
    "get_db_session",
    "init_db",
    "setup_logging",
    # Infra constants & helpers
    "FRESHNESS_TOLERANCE_MINUTES",
    "MIN_ASSET_VALUE_USD",
    "PRICE_CACHE_MAXSIZE",
    "PRICE_CACHE_TTL",
    "REQUIRED_DATA_COLUMNS",
    "ensure_utc_series",
    "ensure_utc_timestamp",
    "parse_period_to_date_range",
    "validate_dataframe_columns",
    # Backtest + tuning
    "backtest_bot",
    "_get_backtest_period",
    "get_default_param_grid",
    "tune_hyperparameters",
]

