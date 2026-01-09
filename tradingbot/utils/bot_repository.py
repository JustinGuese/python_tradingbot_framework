"""Repository for bot database operations."""

from datetime import datetime
from typing import Optional

from .db import Bot as BotModel
from .db import Trade, get_db_session


class BotRepository:
    """Handles database operations for Bot entities."""
    
    @staticmethod
    def create_or_get_bot(name: str) -> BotModel:
        """
        Create or retrieve bot from database.
        
        Args:
            name: Bot name
            
        Returns:
            BotModel instance (detached from session but with attributes loaded)
        """
        with get_db_session() as session:
            bot = session.query(BotModel).filter_by(name=name).first()
            if not bot:
                bot = BotModel(name=name)
                session.add(bot)
                session.flush()  # Flush to get the ID, but let context manager commit
                session.refresh(bot)
            # Access portfolio to ensure it's loaded before expunging
            _ = bot.portfolio
            # Expunge the instance so it can be used outside the session
            # This detaches it but keeps loaded attributes accessible
            session.expunge(bot)
            return bot
    
    @staticmethod
    def update_bot(bot: BotModel) -> BotModel:
        """
        Update bot state in database.
        
        Args:
            bot: BotModel instance to update
            
        Returns:
            Updated BotModel instance
        """
        with get_db_session() as session:
            session.merge(bot)
            # Context manager will commit automatically
            return bot
    
    @staticmethod
    def log_trade(
        bot_name: str,
        symbol: str,
        quantity: float,
        price: float,
        is_buy: bool,
        profit: Optional[float] = None,
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
            
        Returns:
            Created Trade object
        """
        with get_db_session() as session:
            trade = Trade(
                bot_name=bot_name,
                symbol=symbol,
                isBuy=is_buy,
                quantity=float(quantity),
                price=float(price),
                timestamp=datetime.utcnow(),
                profit=float(profit) if profit is not None else None,
            )
            session.add(trade)
            session.flush()  # Flush to get the ID, but let context manager commit
            session.refresh(trade)
            return trade

