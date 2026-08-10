"""
Utility modules for trading bots.

This package now exposes a **layered structure**:

- `utils.core`: core infrastructure (bot base class, DB models, portfolio manager,
  backtesting, hyperparameter tuning, shared helpers).
- `utils.data`: data access (Yahoo Finance + DB, stock fundamentals loaders).
- `utils.portfolio`: portfolio and strategy logic (regime classification and tilts,
  earnings/insider tilting, Sharpe optimisation, portfolio worth analytics, sentiment
  adapters, and the canonical `TRADEABLE` universe).
- `utils.ai`: AI helpers for OpenRouter/LangChain integrations.

For backwards compatibility, the most common core symbols are still re-exported from
the `utils` package root, but new code should prefer the subpackages above.
"""

from .bot_repository import BotRepository
from .botclass import Bot
from .data_service import DataService
from .db import (
    Bot as BotModel,
)
from .db import (
    HistoricData,
    RunLog,
    SessionLocal,
    Trade,
    get_db_session,
)
from .portfolio_manager import PortfolioManager

__all__ = [
    "Bot",
    "BotModel",
    "BotRepository",
    "DataService",
    "HistoricData",
    "PortfolioManager",
    "RunLog",
    "SessionLocal",
    "Trade",
    "get_db_session",
]
