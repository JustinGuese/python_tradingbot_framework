# Database Models

The system uses PostgreSQL with SQLAlchemy ORM. All models are defined in `tradingbot/utils/db.py`.

## Bot Model

Stores bot configuration and portfolio state.

```python
class Bot(Base):
    name: str                    # Primary key
    description: str             # Optional description
    portfolio: dict              # JSON: {"USD": 10000, "QQQ": 5.5, ...}
    created_at: datetime
    updated_at: datetime
```

**Portfolio Format**: `{"USD": cash_amount, "SYMBOL": quantity, ...}`

## Trade Model

Logs all trade executions.

```python
class Trade(Base):
    id: int                      # Auto-increment primary key
    bot_name: str                # Foreign key to Bot.name
    symbol: str                  # Trading symbol
    isBuy: bool                  # True for buy, False for sell
    quantity: float              # Number of shares/units
    price: float                 # Price per unit
    timestamp: datetime           # Execution time
    profit: float                 # Profit (for sells, nullable)
```

## HistoricData Model

Caches market data for performance.

```python
class HistoricData(Base):
    symbol: str                  # Primary key (part of composite)
    timestamp: datetime           # Primary key (part of composite)
    open: float
    high: float
    low: float
    close: float
    volume: float
```

## RunLog Model

Tracks bot execution history.

```python
class RunLog(Base):
    id: int                      # Auto-increment primary key
    bot_name: str                # Foreign key to Bot.name
    start_time: datetime         # When run started
    success: bool                 # Whether run succeeded
    result: str                  # Result message (nullable)
```

## PortfolioWorth Model

Historical portfolio valuations.

```python
class PortfolioWorth(Base):
    bot_name: str                # Primary key (part of composite)
    date: datetime               # Primary key (part of composite)
    portfolio_worth: float        # Total value in USD
    holdings: dict                # JSON snapshot of holdings
    created_at: datetime
```

## Session Management

Always use the context manager:

```python
from tradingbot.utils.db import get_db_session

with get_db_session() as session:
    bot = session.query(Bot).filter_by(name="MyBot").first()
    # Context manager commits automatically
```

The context manager handles:
- Automatic commit on success
- Automatic rollback on exceptions
- Connection retry logic (3 attempts with exponential backoff)
- Proper session cleanup

## Next Steps

- [Database API Reference](../api/database.md) - Complete API docs
- [Architecture Overview](overview.md) - System design
