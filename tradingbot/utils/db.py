import logging
from collections.abc import Generator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from os import environ
from urllib.parse import quote_plus

from sqlalchemy import (
    JSON,
    Boolean,
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
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

logger = logging.getLogger(__name__)


def _utcnow_naive() -> datetime:
    """Current UTC time as a NAIVE datetime — the value `datetime.utcnow()` returned.

    Every timestamp column here is a bare `DateTime` (no `timezone=True`), and the
    rows already in production are naive UTC, so the default must stay naive or new
    rows would stop comparing against old ones.

    Two things this replaces. First, `default=datetime.utcnow` as a bare callable:
    still deprecated in 3.12 and still emitting a DeprecationWarning from inside
    SQLAlchemy on every insert, because passing the function rather than calling it
    only moves the call, it does not avoid it. Second, one outlier column that used
    `lambda: datetime.now(UTC)` — an *aware* datetime into a naive column, which
    happened to round-trip correctly only because the value was already UTC.
    """
    return datetime.now(UTC).replace(tzinfo=None)


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
    raise KeyError("Set POSTGRES_URI or (POSTGRES_HOST + POSTGRES_PASSWORD) for database connection")


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


class Base(DeclarativeBase):
    pass


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

    name: Mapped[str] = mapped_column(String, primary_key=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    portfolio: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSON), nullable=True, default=lambda: {"USD": 10000})
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=_utcnow_naive)
    # onupdate, not just default: without it the column is stamped once at INSERT
    # and never moves, so it recorded creation time under an "updated" name. That
    # made it useless for the one question it gets asked — "is this bot still
    # trading?" — e.g. SynthesizedHyperConvexityBot traded 2026-08-11 while its
    # updated_at still read 2026-03-22. Rows written before this stay stale; only
    # the next portfolio write corrects each one.
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)


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
        profit: MISNOMER — this is the net cash proceeds credited on a sell,
            NOT realized P&L. No cost basis is tracked anywhere, so summing this
            column does not give profit. NULL on buys.
    """

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_name: Mapped[str | None] = mapped_column(String, ForeignKey("bots.name"))
    symbol: Mapped[str | None] = mapped_column(String)
    isBuy: Mapped[bool | None] = mapped_column(Boolean)
    quantity: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    timestamp: Mapped[datetime | None] = mapped_column(DateTime, default=_utcnow_naive)
    profit: Mapped[float | None] = mapped_column(Float, nullable=True)


class HistoricData(Base):
    """
    Historic market data model for storing OHLCV data.

    `interval` is part of the primary key, and must stay that way. Without it the
    key is (symbol, timestamp), which lets a symbol's 1-minute and daily bars
    collide in one pile: xauzenbot writes ^XAU at 1m every 5 minutes, so a later
    request for interval="1d" silently returned 74k one-minute rows and every TA
    indicator computed on top of them was meaningless. Reads MUST filter on
    interval, and writes MUST set it.

    Attributes:
        symbol: Trading symbol (primary key, part of composite key)
        interval: Bar size the row was fetched at, e.g. "1m", "1d", "1wk"
                  (primary key, part of composite key)
        timestamp: Timestamp of the data point (primary key, part of composite key)
        open: Opening price
        high: Highest price
        low: Lowest price
        close: Closing price
        volume: Trading volume
    """

    __tablename__ = "historic_data"

    symbol: Mapped[str] = mapped_column(String, primary_key=True)
    interval: Mapped[str] = mapped_column(String, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)


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

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_name: Mapped[str | None] = mapped_column(String, ForeignKey("bots.name"))
    start_time: Mapped[datetime | None] = mapped_column(DateTime, default=_utcnow_naive)
    success: Mapped[bool | None] = mapped_column(Boolean, default=False)
    result: Mapped[str | None] = mapped_column(String, nullable=True)


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

    bot_name: Mapped[str] = mapped_column(String, ForeignKey("bots.name"), primary_key=True)
    date: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    portfolio_worth: Mapped[float] = mapped_column(Float, nullable=False)
    holdings: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSON), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=_utcnow_naive)


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

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    link: Mapped[str] = mapped_column(String, nullable=False)
    publisher: Mapped[str | None] = mapped_column(String, nullable=True)
    publisher_url: Mapped[str | None] = mapped_column(String, nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    related_tickers: Mapped[list | None] = mapped_column(JSON, nullable=True)
    acted_on: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=_utcnow_naive)


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
    __table_args__ = (UniqueConstraint("symbol", "report_date", name="uq_stock_earnings_symbol_report_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False, index=True)
    report_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    eps_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    reported_eps: Mapped[float | None] = mapped_column(Float, nullable=True)
    surprise_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    fiscal_period: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=_utcnow_naive)


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

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False, index=True)
    transaction_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    insider_name: Mapped[str | None] = mapped_column(String, nullable=True)
    transaction_type: Mapped[str | None] = mapped_column(String, nullable=True)
    shares: Mapped[float | None] = mapped_column(Float, nullable=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=_utcnow_naive)


class BacktestResult(Base):
    __tablename__ = "backtest_results"
    __table_args__ = (UniqueConstraint("bot_name", "symbol", "interval", "metric", name="uq_backtest_results_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    symbol: Mapped[str | None] = mapped_column(String, nullable=True)
    interval: Mapped[str | None] = mapped_column(String, nullable=True)
    period: Mapped[str | None] = mapped_column(String, nullable=True)
    metric: Mapped[str] = mapped_column(String, nullable=False)  # "best_sharpe" or "best_yearly_return"
    params: Mapped[dict | None] = mapped_column(MutableDict.as_mutable(JSON), default=lambda: {})
    yearly_return: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    sharpe_ratio: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    nrtrades: Mapped[int | None] = mapped_column(Integer, nullable=True)
    maxdrawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    buy_hold_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    sortino_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    calmar_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)  # fraction 0.0–1.0
    volatility: Mapped[float | None] = mapped_column(Float, nullable=True)  # annualized
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=_utcnow_naive)


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

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False, index=True)
    model_name: Mapped[str] = mapped_column(String, nullable=False)
    interval: Mapped[str] = mapped_column(String, nullable=False)
    prediction_made_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    target_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    predicted_open: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_high: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_low: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_close: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)


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

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String, nullable=False, index=True)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str | None] = mapped_column(String, nullable=True)
    summary: Mapped[str | None] = mapped_column(String, nullable=True)
    symbol: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    acted_on: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=_utcnow_naive)


class LiveEquity(Base):
    """
    Real broker/vault equity snapshots — one row per (broker, account, UTC day).

    Deliberately NOT stored in portfolio_worth. That table is the paper-bot
    leaderboard: it has a foreign key to bots.name, every bot starts at $10k, and
    no row there pays fees or funding. A real-money curve in the same table would
    need a fake bots row (which calculate_portfolio_worth would then overwrite
    daily with a paper valuation) and would make both curves uninterpretable.

    No foreign key here — a vault is not a bot.

    Attributes:
        broker: Adapter name, e.g. "hyperliquid"
        account_id: Vault or account address the equity belongs to
        date: Midnight UTC — the idempotency key, mirroring PortfolioWorth
        timestamp: Actual snapshot time (UTC)
        equity: Total account value including unrealized PnL
        cash: Free collateral / withdrawable, if the broker reports it
        positions: JSON of broker_symbol -> signed quantity at snapshot time
        bot_weights: JSON string of the LIVETRADE_BOT_WEIGHTS that were live
        is_testnet: Keeps testnet validation runs out of the published curve
    """

    __tablename__ = "live_equity"
    __table_args__ = (
        UniqueConstraint("broker", "account_id", "date", name="uq_live_equity_broker_account_date"),
        Index("ix_live_equity_broker_date", "broker", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    broker: Mapped[str] = mapped_column(String, nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(String, nullable=False)
    date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    equity: Mapped[float] = mapped_column(Float, nullable=False)
    cash: Mapped[float | None] = mapped_column(Float, nullable=True)
    positions: Mapped[dict | None] = mapped_column(MutableDict.as_mutable(JSON), nullable=True)
    bot_weights: Mapped[str | None] = mapped_column(String, nullable=True)
    is_testnet: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=_utcnow_naive)


def _migrate_schema() -> None:
    """
    Apply incremental column additions that create_all cannot handle on existing tables.
    Each statement is idempotent (IF NOT EXISTS), safe to run on every startup.

    NOTE: brand-new tables need nothing here — create_all() creates them. Only
    new *columns* on *existing* tables belong below.
    """
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE stock_news ADD COLUMN IF NOT EXISTS acted_on BOOLEAN NOT NULL DEFAULT FALSE"))
        # BacktestResult new metrics
        conn.execute(text("ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS sortino_ratio FLOAT"))
        conn.execute(text("ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS calmar_ratio FLOAT"))
        conn.execute(text("ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS win_rate FLOAT"))
        conn.execute(text("ALTER TABLE backtest_results ADD COLUMN IF NOT EXISTS volatility FLOAT"))

        # historic_data.interval: add the column, infer it for pre-existing rows,
        # and widen the primary key. Guarded on the PK still being the 2-column
        # form, so this is a no-op on an already-migrated or freshly-created DB.
        #
        # The interval of a legacy row is inferred from the smallest gap to an
        # adjacent bar of the same symbol. Time-of-day does NOT work: many daily
        # bars are stamped at session open rather than midnight.
        conn.execute(text("ALTER TABLE historic_data ADD COLUMN IF NOT EXISTS interval VARCHAR"))
        conn.execute(
            text("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = 'historic_data'::regclass AND contype = 'p'
                  AND pg_get_constraintdef(oid) = 'PRIMARY KEY (symbol, "timestamp")'
            ) THEN
                WITH g AS (
                    SELECT symbol, timestamp,
                           EXTRACT(EPOCH FROM (timestamp - lag(timestamp) OVER w))::bigint AS prev_gap,
                           EXTRACT(EPOCH FROM (lead(timestamp) OVER w - timestamp))::bigint AS next_gap
                    FROM historic_data
                    WINDOW w AS (PARTITION BY symbol ORDER BY timestamp)
                ), c AS (
                    SELECT symbol, timestamp,
                           LEAST(COALESCE(prev_gap, 999999999),
                                 COALESCE(next_gap, 999999999)) AS min_gap
                    FROM g
                )
                UPDATE historic_data h SET interval = CASE
                        WHEN c.min_gap <= 300 THEN '1m'
                        ELSE '1d'
                    END
                FROM c WHERE h.symbol = c.symbol AND h.timestamp = c.timestamp;

                ALTER TABLE historic_data ALTER COLUMN interval SET NOT NULL;
                ALTER TABLE historic_data DROP CONSTRAINT historic_data_pkey;
                ALTER TABLE historic_data ADD PRIMARY KEY (symbol, interval, timestamp);
            END IF;
        END $$;
        """)
        )
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
            with suppress(Exception):
                session.rollback()
        logger.error(f"Unexpected error in database session: {type(e).__name__}: {e}")
        raise
    finally:
        if session:
            with suppress(Exception):
                session.close()
