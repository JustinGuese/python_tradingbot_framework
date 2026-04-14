import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from os import environ
from typing import Generator
from urllib.parse import quote_plus

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)


def _database_url() -> str:
    """Build database URL from POSTGRES_URI or from cluster components (POSTGRES_HOST, etc.)."""
    uri = environ.get("POSTGRES_URI")
    if uri:
        return "postgresql+psycopg2://" + uri
    host = environ.get("POSTGRES_HOST")
    if host:
        user = environ.get("POSTGRES_USER", "postgres")
        password = environ.get("POSTGRES_PASSWORD", "")
        port = environ.get("POSTGRES_PORT", "5432")
        database = environ.get("POSTGRES_DATABASE", "postgres")
        # Quote password for special characters (e.g. &, $)
        user_esc = quote_plus(user)
        password_esc = quote_plus(password)
        uri = f"{user_esc}:{password_esc}@{host}:{port}/{database}"
        return "postgresql+psycopg2://" + uri
    raise KeyError(
        "Set POSTGRES_URI or (POSTGRES_HOST + POSTGRES_PASSWORD) for database connection"
    )


DATABASE_URL = _database_url()
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
    },
    # echo=True # debugging
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


### MODELS
class Bot(Base):
    """
    Bot model representing a trading bot instance.
    
    Attributes:
        name: Unique bot name (primary key)
        description: Optional description of the bot
        portfolio: JSON dictionary representing portfolio holdings (default: {"USD": 10000})
                  Format: {"USD": cash_amount, "SYMBOL": quantity, ...}
        created_at: Timestamp when bot was created
        updated_at: Timestamp when bot was last updated
    """
    __tablename__ = "bots"

    name = Column(String, primary_key=True)
    description = Column(String, nullable=True)
    portfolio = Column(MutableDict.as_mutable(JSON), default=lambda: {"USD": 10000})
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class Trade(Base):
    """
    Trade model representing a single trade execution.
    
    Attributes:
        id: Auto-incrementing trade ID (primary key)
        bot_name: Name of the bot that executed the trade (foreign key to Bot.name)
        symbol: Trading symbol (e.g., "QQQ", "EURUSD=X")
        isBuy: True for buy orders, False for sell orders
        quantity: Number of shares/units traded
        price: Price per unit at time of trade
        timestamp: Timestamp when trade was executed
        profit: Profit from the trade (for sells, nullable)
    """
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    bot_name = Column(String, ForeignKey("bots.name"))
    symbol = Column(String)
    isBuy = Column(Boolean)
    quantity = Column(Float)
    price = Column(Float)
    timestamp = Column(DateTime, default=datetime.utcnow)
    profit = Column(Float, nullable=True)


class HistoricData(Base):
    """
    Historic market data model for storing OHLCV data.
    
    Attributes:
        symbol: Trading symbol (primary key, part of composite key)
        timestamp: Timestamp of the data point (primary key, part of composite key)
        open: Opening price
        high: Highest price
        low: Lowest price
        close: Closing price
        volume: Trading volume
    """
    __tablename__ = "historic_data"

    symbol = Column(String, primary_key=True)
    timestamp = Column(DateTime, primary_key=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)


class RunLog(Base):
    """
    Run log model for tracking bot execution history.
    
    Attributes:
        id: Auto-incrementing log ID (primary key)
        bot_name: Name of the bot (foreign key to Bot.name)
        start_time: Timestamp when the run started
        success: Whether the run completed successfully
        result: Result message (nullable, contains decision/error info)
    """
    __tablename__ = "run_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    bot_name = Column(String, ForeignKey("bots.name"))
    start_time = Column(DateTime, default=datetime.utcnow)
    success = Column(Boolean, default=False)
    result = Column(String, nullable=True)


class PortfolioWorth(Base):
    """
    Portfolio worth model for tracking portfolio value over time.

    Attributes:
        bot_name: Name of the bot (primary key, part of composite key, foreign key to Bot.name)
        date: Date of the portfolio valuation (primary key, part of composite key)
        portfolio_worth: Total portfolio value in USD
        holdings: JSON dictionary of holdings at this date
        created_at: Timestamp when this record was created
    """
    __tablename__ = "portfolio_worth"

    bot_name = Column(String, ForeignKey("bots.name"), primary_key=True)
    date = Column(DateTime, primary_key=True)
    portfolio_worth = Column(Float, nullable=False)
    holdings = Column(MutableDict.as_mutable(JSON), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class StockNews(Base):
    """
    Stock news model for storing news articles per symbol from yfinance.

    Attributes:
        symbol: Trading symbol
        title: Article title
        link: Article URL (unique per symbol)
        publisher: Publisher name (nullable)
        publisher_url: Publisher URL (nullable)
        published_at: When the article was published (UTC)
        related_tickers: JSON array of related tickers (nullable)
        created_at: When this record was created
    """
    __tablename__ = "stock_news"
    __table_args__ = (
        UniqueConstraint("symbol", "link", name="uq_stock_news_symbol_link"),
        Index("ix_stock_news_symbol_published_at", "symbol", "published_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    link = Column(String, nullable=False)
    publisher = Column(String, nullable=True)
    publisher_url = Column(String, nullable=True)
    published_at = Column(DateTime, nullable=False)
    related_tickers = Column(JSON, nullable=True)
    acted_on = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class StockEarnings(Base):
    """
    Stock earnings model for storing earnings dates and results from yfinance.

    Attributes:
        symbol: Trading symbol
        report_date: Earnings report date
        eps_estimate: Estimated EPS (nullable)
        reported_eps: Reported EPS (nullable)
        surprise_pct: Surprise percentage (nullable)
        fiscal_period: Fiscal period if available (nullable)
        created_at: When this record was created
    """
    __tablename__ = "stock_earnings"
    __table_args__ = (
        UniqueConstraint("symbol", "report_date", name="uq_stock_earnings_symbol_report_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False, index=True)
    report_date = Column(DateTime, nullable=False)
    eps_estimate = Column(Float, nullable=True)
    reported_eps = Column(Float, nullable=True)
    surprise_pct = Column(Float, nullable=True)
    fiscal_period = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class StockInsiderTrade(Base):
    """
    Stock insider trade model for storing insider transactions from yfinance.

    Attributes:
        symbol: Trading symbol
        transaction_date: Date of the transaction
        insider_name: Name of the insider (nullable)
        transaction_type: Type e.g. Purchase, Sale (nullable)
        shares: Number of shares (nullable)
        value: Transaction value if available (nullable)
        created_at: When this record was created
    """
    __tablename__ = "stock_insider_trades"
    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "transaction_date",
            "insider_name",
            "transaction_type",
            "shares",
            name="uq_stock_insider_trades_key",
        ),
        Index("ix_stock_insider_trades_symbol_transaction_date", "symbol", "transaction_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False, index=True)
    transaction_date = Column(DateTime, nullable=False)
    insider_name = Column(String, nullable=True)
    transaction_type = Column(String, nullable=True)
    shares = Column(Float, nullable=True)
    value = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class BacktestResult(Base):
    __tablename__ = "backtest_results"
    __table_args__ = (
        UniqueConstraint("bot_name", "symbol", "interval", "metric", name="uq_backtest_results_key"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    bot_name = Column(String, nullable=False, index=True)
    symbol = Column(String, nullable=True)
    interval = Column(String, nullable=True)
    period = Column(String, nullable=True)
    metric = Column(String, nullable=False)  # "best_sharpe" or "best_yearly_return"
    params = Column(MutableDict.as_mutable(JSON), default=lambda: {})
    yearly_return = Column(Float, nullable=True, index=True)
    sharpe_ratio = Column(Float, nullable=True, index=True)
    nrtrades = Column(Integer, nullable=True)
    maxdrawdown = Column(Float, nullable=True)
    buy_hold_return = Column(Float, nullable=True)
    sortino_ratio = Column(Float, nullable=True)
    calmar_ratio = Column(Float, nullable=True)
    win_rate = Column(Float, nullable=True)  # fraction 0.0–1.0
    volatility = Column(Float, nullable=True)  # annualized
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class KronosPrediction(Base):
    """
    Kronos foundation-model OHLCV forecast for a future date.

    Written daily by kronosbot after market close.
    Bots can query this table to use model-based price signals.

    Attributes:
        id: Auto-incrementing primary key
        symbol: Trading symbol (e.g. "SPY", "AAPL", "EURUSD=X")
        model_name: HuggingFace model ID used (e.g. "NeoQuasar/Kronos-mini")
        interval: Input OHLCV interval (e.g. "1d")
        prediction_made_at: UTC timestamp when inference was run
        target_date: The future date being forecast
        predicted_open: Predicted open price
        predicted_high: Predicted high price
        predicted_low: Predicted low price
        predicted_close: Predicted close price
        predicted_volume: Predicted volume (nullable — may be zero for some symbols)
        horizon_days: Steps ahead this row represents (1 = tomorrow, 5 = five days out)
    """
    __tablename__ = "kronos_predictions"
    __table_args__ = (
        UniqueConstraint("symbol", "target_date", "model_name", name="uq_kronos_predictions_key"),
        Index("ix_kronos_predictions_symbol_target_date", "symbol", "target_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False, index=True)
    model_name = Column(String, nullable=False)
    interval = Column(String, nullable=False)
    prediction_made_at = Column(DateTime, nullable=False)
    target_date = Column(DateTime, nullable=False)
    predicted_open = Column(Float, nullable=False)
    predicted_high = Column(Float, nullable=False)
    predicted_low = Column(Float, nullable=False)
    predicted_close = Column(Float, nullable=False)
    predicted_volume = Column(Float, nullable=True)
    horizon_days = Column(Integer, nullable=False)


class TelegramMessage(Base):
    """
    Telegram channel message model for storing monitored channel messages and AI summaries.

    Attributes:
        id: Auto-incrementing primary key
        channel: Telegram channel username or ID (e.g. "mychannel" or "-1001234567890")
        message_id: Telegram message ID (unique per channel)
        text: Original message text (nullable for media-only messages)
        summary: AI-generated summary of the message (nullable)
        symbol: Primary stock/asset ticker extracted by AI (e.g. "AAPL", "BTC", nullable)
        published_at: When the message was posted in Telegram (UTC)
        created_at: When this record was created
    """
    __tablename__ = "telegram_messages"
    __table_args__ = (
        UniqueConstraint("channel", "message_id", name="uq_telegram_messages_channel_message_id"),
        Index("ix_telegram_messages_channel_published_at", "channel", "published_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel = Column(String, nullable=False, index=True)
    message_id = Column(Integer, nullable=False)
    text = Column(String, nullable=True)
    summary = Column(String, nullable=True)
    symbol = Column(String, nullable=True, index=True)
    acted_on = Column(Boolean, nullable=False, default=False)
    published_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


def _migrate_schema() -> None:
    """
    Apply incremental column additions that create_all cannot handle on existing tables.
    Each statement is idempotent (IF NOT EXISTS), safe to run on every startup.
    """
    with engine.connect() as conn:
        conn.execute(text(
            "ALTER TABLE stock_news "
            "ADD COLUMN IF NOT EXISTS acted_on BOOLEAN NOT NULL DEFAULT FALSE"
        ))
        # BacktestResult new metrics
        conn.execute(text("ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS sortino_ratio FLOAT"))
        conn.execute(text("ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS calmar_ratio FLOAT"))
        conn.execute(text("ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS win_rate FLOAT"))
        conn.execute(text("ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS volatility FLOAT"))
        conn.commit()


def init_db() -> None:
    """Initialize database tables and run schema migrations."""
    Base.metadata.create_all(engine)
    _migrate_schema()


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """
    Simple context manager for database sessions.
    
    Ensures proper session cleanup and rollback on exceptions.
    NOTE: We intentionally avoid internal retry loops here because a
    @contextmanager generator must yield exactly once; retry logic is
    better handled at call sites if needed.
    
    Usage:
        with get_db_session() as session:
            # Use session here
            session.query(Bot).all()
    """
    session: Session | None = None
    try:
        session = SessionLocal()
        yield session
        session.commit()
    except Exception as e:
        if session:
            try:
                session.rollback()
            except Exception:
                pass
        logger.error(f"Unexpected error in database session: {type(e).__name__}: {e}")
        raise
    finally:
        if session:
            try:
                session.close()
            except Exception:
                pass
