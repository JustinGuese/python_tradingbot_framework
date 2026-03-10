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

## StockNews Model

News articles per symbol from yfinance (loaded daily with portfolio worth).

```python
class StockNews(Base):
    id: int                      # Auto-increment primary key
    symbol: str                  # Trading symbol (indexed)
    title: str                   # Article title
    link: str                    # Article URL
    publisher: str               # Publisher name (nullable)
    publisher_url: str           # Publisher URL (nullable)
    published_at: datetime       # When the article was published (UTC)
    related_tickers: list        # JSON array of related tickers (nullable)
    created_at: datetime
```

**Unique constraint**: `(symbol, link)` so the same article is not stored twice for a symbol. Index on `(symbol, published_at)` for efficient queries.

## StockEarnings Model

Earnings dates and results per symbol from yfinance (loaded daily with portfolio worth).

```python
class StockEarnings(Base):
    id: int                      # Auto-increment primary key
    symbol: str                  # Trading symbol (indexed)
    report_date: datetime        # Earnings report date
    eps_estimate: float          # Estimated EPS (nullable)
    reported_eps: float          # Reported EPS (nullable)
    surprise_pct: float          # Surprise percentage (nullable)
    fiscal_period: str           # Fiscal period if available (nullable)
    created_at: datetime
```

**Unique constraint**: `(symbol, report_date)` to avoid duplicate earnings rows. Index on `symbol`.

## StockInsiderTrade Model

Insider transactions per symbol from yfinance (loaded daily with portfolio worth).

```python
class StockInsiderTrade(Base):
    id: int                      # Auto-increment primary key
    symbol: str                  # Trading symbol (indexed)
    transaction_date: datetime   # Date of the transaction
    insider_name: str            # Name of the insider (nullable)
    transaction_type: str        # Type e.g. Purchase, Sale (nullable)
    shares: float                # Number of shares (nullable)
    value: float                 # Transaction value if available (nullable)
    created_at: datetime
```

**Unique constraint**: `(symbol, transaction_date, insider_name, transaction_type, shares)`. Index on `(symbol, transaction_date)`.

## TelegramMessage Model

Monitored Telegram channel messages with AI summaries (written by the Telegram monitor CronJob).

```python
class TelegramMessage(Base):
    id: int                  # Auto-increment primary key
    channel: str             # Channel username or ID (indexed)
    message_id: int          # Telegram message ID (unique per channel)
    text: str                # Original message text (nullable, max 4000 chars)
    summary: str             # AI-generated 1-3 sentence summary (nullable)
    symbol: str              # Primary ticker extracted by AI e.g. "AAPL" (nullable, indexed)
    acted_on: bool           # True once the signals bot has evaluated this message (default False)
    published_at: datetime   # When the message was posted in Telegram (UTC)
    created_at: datetime
```

**Unique constraint**: `(channel, message_id)` — same message never stored twice.
**Indexes**: `(channel, published_at)`, `symbol` — efficient queries by symbol or channel timeline.

`acted_on` is set to `True` by `telegram_signalsbankbot` **before** the AI classification call — crash-safe deduplication without a separate tracking table.

See [Telegram Monitor Guide](../guides/telegram-monitor.md) and [Telegram Signals Bot Guide](../guides/telegram-signals-bot.md) for setup and usage.

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
