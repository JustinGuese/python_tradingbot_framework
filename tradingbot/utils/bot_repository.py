"""Repository for bot database operations."""

from datetime import datetime

from sqlalchemy.orm import Session

from .db import Bot as BotModel
from .db import RunLog, Trade, get_db_session


class BotRepository:
    """Handles database operations for Bot entities."""

    @staticmethod
    def read_portfolio(name: str, session: Session | None = None) -> dict | None:
        """
        Read another bot's portfolio WITHOUT creating it.

        create_or_get_bot() would happily materialise a fresh $10k bot row for a
        typo'd name, which a reader must never do — meta-bots that mirror a parent
        need to tell "parent is all cash" apart from "parent does not exist".

        Args:
            name: Bot name
            session: Optional existing database session

        Returns:
            A plain dict copy of the portfolio, or None if the bot has no row.
        """

        def _read(sess: Session) -> dict | None:
            bot = sess.query(BotModel).filter_by(name=name).first()
            return dict(bot.portfolio or {}) if bot else None

        if session:
            return _read(session)

        with get_db_session() as session:
            return _read(session)

    @staticmethod
    def last_successful_run(name: str, session: Session | None = None) -> datetime | None:
        """
        Timestamp of a bot's most recent successful run, or None if it never had one.

        Used to detect a parent whose CronJob has died: its portfolio row keeps
        returning the last state it traded into, which looks like a live signal.

        Args:
            name: Bot name
            session: Optional existing database session

        Returns:
            Naive UTC datetime of the last RunLog row with success=True, or None.
        """

        def _read(sess: Session) -> datetime | None:
            row = (
                sess.query(RunLog.start_time)
                .filter(RunLog.bot_name == name, RunLog.success.is_(True))
                .order_by(RunLog.start_time.desc())
                .first()
            )
            return row[0] if row else None

        if session:
            return _read(session)

        with get_db_session() as session:
            return _read(session)

    @staticmethod
    def create_or_get_bot(name: str, session: Session | None = None) -> BotModel:
        """
        Create or retrieve bot from database.

        Args:
            name: Bot name
            session: Optional existing database session

        Returns:
            BotModel instance
        """

        def _get_or_create(sess: Session):
            bot = sess.query(BotModel).filter_by(name=name).first()
            if not bot:
                bot = BotModel(name=name)
                sess.add(bot)
                sess.flush()
                sess.refresh(bot)
            _ = bot.portfolio
            return bot

        if session:
            return _get_or_create(session)

        with get_db_session() as session:
            bot = _get_or_create(session)
            session.expunge(bot)
            return bot

    @staticmethod
    def get_bot_locked(session: Session, name: str) -> BotModel:
        """
        Get a bot by name with a row-level lock (FOR UPDATE).
        MUST be called within an active transaction.

        Args:
            session: Active database session
            name: Bot name

        Returns:
            Bot model instance
        """
        return session.query(BotModel).filter_by(name=name).with_for_update().one()

    @staticmethod
    def update_bot(bot: BotModel, session: Session | None = None) -> BotModel:
        """
        Update bot state in database.

        Args:
            bot: BotModel instance to update
            session: Optional existing database session

        Returns:
            Updated BotModel instance
        """
        if session:
            session.add(bot)
            session.flush()
            return bot

        with get_db_session() as session:
            session.merge(bot)
            return bot

    @staticmethod
    def log_trade(
        bot_name: str,
        symbol: str,
        quantity: float,
        price: float,
        is_buy: bool,
        profit: float | None = None,
        session: Session | None = None,
    ) -> Trade:
        """
        Log a trade to the database.

        Args:
            bot_name: Name of the bot executing the trade
            symbol: Trading symbol
            quantity: Number of shares/units
            price: Price per unit
            is_buy: True for buy, False for sell
            profit: MISNOMER — net cash proceeds credited on a sell, NOT realized
                P&L (no cost basis is tracked). Leave None on buys.
            session: Optional existing database session

        Returns:
            Created Trade object
        """

        def _create_trade(sess: Session):
            trade = Trade(
                bot_name=bot_name,
                symbol=symbol,
                isBuy=is_buy,
                quantity=float(quantity),
                price=float(price),
                timestamp=datetime.utcnow(),
                profit=float(profit) if profit is not None else None,
            )
            sess.add(trade)
            sess.flush()
            sess.refresh(trade)
            return trade

        if session:
            return _create_trade(session)

        with get_db_session() as session:
            return _create_trade(session)
