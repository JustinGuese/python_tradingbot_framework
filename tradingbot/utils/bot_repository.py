"""Repository for bot database operations."""

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from .db import Bot as BotModel
from .db import Trade, get_db_session


class BotRepository:
    """Handles database operations for Bot entities."""
    
    @staticmethod
    def create_or_get_bot(name: str, session: Optional[Session] = None) -> BotModel:
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
    def update_bot(bot: BotModel, session: Optional[Session] = None) -> BotModel:
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
        profit: Optional[float] = None,
        session: Optional[Session] = None,
    ) -> Trade:
        """
        Log a trade to the database.
        
        Args:
            bot_name: Name of the bot executing the trade
            symbol: Trading symbol
            quantity: Number of shares/units
            price: Price per unit
            is_buy: True for buy, False for sell
            profit: Profit from the trade (for sells)
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


