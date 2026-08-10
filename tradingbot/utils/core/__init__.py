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

from livetrade import (
    Collective2Broker,
    LiveBroker,
    LiveTradeCopier,
)

from ..backtest import _get_backtest_period, backtest_bot
from ..bot_repository import BotRepository
from ..botclass import Bot
from ..config import (
    FRESHNESS_TOLERANCE_MINUTES,
    MIN_ASSET_VALUE_USD,
    PRICE_CACHE_MAXSIZE,
    PRICE_CACHE_TTL,
    REQUIRED_DATA_COLUMNS,
    setup_logging,
)
from ..db import (
    DATABASE_URL,
    Base,
    HistoricData,
    LiveEquity,
    PortfolioWorth,
    RunLog,
    SessionLocal,
    StockEarnings,
    StockInsiderTrade,
    StockNews,
    Trade,
    engine,
    get_db_session,
    init_db,
)
from ..db import (
    Bot as BotModel,
)
from ..helpers import (
    ensure_utc_series,
    ensure_utc_timestamp,
    parse_period_to_date_range,
    validate_dataframe_columns,
)
from ..hyperparameter_tuning import (
    get_default_param_grid,
    tune_hyperparameters,
)
from ..kronos_client import KronosClient, kronos_forecast
from ..portfolio_manager import PortfolioManager

__all__ = [
    "DATABASE_URL",
    "FRESHNESS_TOLERANCE_MINUTES",
    "MIN_ASSET_VALUE_USD",
    "PRICE_CACHE_MAXSIZE",
    "PRICE_CACHE_TTL",
    "REQUIRED_DATA_COLUMNS",
    "Base",
    "Bot",
    "BotModel",
    "BotRepository",
    "Collective2Broker",
    "HistoricData",
    "KronosClient",
    "LiveBroker",
    "LiveEquity",
    "LiveTradeCopier",
    "PortfolioManager",
    "PortfolioWorth",
    "RunLog",
    "SessionLocal",
    "StockEarnings",
    "StockInsiderTrade",
    "StockNews",
    "Trade",
    "_get_backtest_period",
    "backtest_bot",
    "engine",
    "ensure_utc_series",
    "ensure_utc_timestamp",
    "get_db_session",
    "get_default_param_grid",
    "init_db",
    "kronos_forecast",
    "parse_period_to_date_range",
    "setup_logging",
    "tune_hyperparameters",
    "validate_dataframe_columns",
]
