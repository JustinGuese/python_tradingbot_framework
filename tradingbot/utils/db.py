import logging
import time
from contextlib import contextmanager
from datetime import datetime
from os import environ
from typing import Generator

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    create_engine,
)
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)

DATABASE_URL = "postgresql+psycopg2://" + environ["POSTGRES_URI"]
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


Base.metadata.create_all(engine)


@contextmanager
def get_db_session(max_retries: int = 3, retry_delay: float = 1.0) -> Generator[Session, None, None]:
    """
    Context manager for database sessions with retry logic.
    
    Ensures proper session cleanup and rollback on exceptions.
    Automatically retries on transient connection errors.
    
    Args:
        max_retries: Maximum number of retry attempts (default: 3)
        retry_delay: Initial delay between retries in seconds (default: 1.0)
    
    Usage:
        with get_db_session() as session:
            # Use session here
            session.query(Bot).all()
    """
    session = None
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            session = SessionLocal()
            yield session
            session.commit()
            return  # Success, exit retry loop
        except (OperationalError, SQLAlchemyError) as e:
            # These are potentially transient errors that might benefit from retry
            if session:
                try:
                    session.rollback()
                except Exception:
                    pass  # Ignore rollback errors during retry
                finally:
                    try:
                        session.close()
                    except Exception:
                        pass  # Ignore close errors during retry
            
            last_exception = e
            is_last_attempt = attempt == max_retries - 1
            
            if is_last_attempt:
                logger.error(
                    f"Database error after {max_retries} attempts: "
                    f"{type(e).__name__}: {e}"
                )
                raise
            else:
                # Exponential backoff: delay increases with each retry
                delay = retry_delay * (2 ** attempt)
                logger.warning(
                    f"Database error (attempt {attempt + 1}/{max_retries}): "
                    f"{type(e).__name__}: {e}. Retrying in {delay:.2f}s..."
                )
                time.sleep(delay)
        except Exception as e:
            # Non-retryable errors - close session and raise immediately
            if session:
                try:
                    session.rollback()
                except Exception:
                    pass
                finally:
                    try:
                        session.close()
                    except Exception:
                        pass
            logger.error(f"Unexpected error in database session: {type(e).__name__}: {e}")
            raise
    
    # Should never reach here, but just in case
    if last_exception:
        raise last_exception
