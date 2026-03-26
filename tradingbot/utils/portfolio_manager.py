from typing import Optional

import logging

import pandas as pd
from sqlalchemy.orm import Session

from .bot_repository import BotRepository
from .data_service import DataService
from .db import Bot as BotModel
from .db import get_db_session
from .config import PORTFOLIO_CONFIG

logger = logging.getLogger(__name__)


class PortfolioManager:
    """Manages portfolio operations including buying, selling, and rebalancing."""
    
    def __init__(self, bot: BotModel, bot_name: str, data_service: DataService, bot_repository: BotRepository):
        """
        Initialize portfolio manager.
        
        Args:
            bot: BotModel instance representing the bot's portfolio
            bot_name: Name of the bot (passed separately to avoid DetachedInstanceError)
            data_service: DataService instance for fetching prices
            bot_repository: BotRepository instance for database operations
        """
        self.bot = bot
        self.bot_name = bot_name
        self.data_service = data_service
        self.bot_repository = bot_repository

    def _refresh_bot(self, session: Optional[Session] = None) -> None:
        """Ensure the Bot instance is attached to an active session."""
        self.bot = self.bot_repository.create_or_get_bot(self.bot_name, session=session)
    
    def buy(
        self,
        symbol: str,
        quantity_usd: float = -1,
        cached_data: Optional[pd.DataFrame] = None,
        refresh: bool = True,
        session: Optional[Session] = None,
    ) -> None:
        """
        Buy a quantity of the specified symbol.
        
        Args:
            symbol: Trading symbol to buy
            quantity_usd: Amount in USD to spend (-1 means use all available cash)
            cached_data: Optional cached DataFrame for price lookup
            refresh: Whether to refresh the bot from DB before executing
            session: Optional existing database session
        """
        def _execute_buy(sess: Session):
            if sess:
                # Lock row if in transaction
                self.bot = self.bot_repository.get_bot_locked(sess, self.bot_name)
            elif refresh:
                self._refresh_bot()

            cash = self.bot.portfolio.get("USD", 0)
            qty_usd = cash if quantity_usd == -1 else quantity_usd

            if qty_usd > cash:
                logger.warning(f"Insufficient cash to buy {symbol}: have ${cash:.2f}, need ${qty_usd:.2f}. Using available cash.")
                qty_usd = cash

            if qty_usd <= 0:
                logger.warning(f"Insufficient cash to buy {symbol}")
                return

            price = self.data_service.get_latest_price(symbol, cached_data)
            quantity = qty_usd / price

            if quantity <= 0:
                logger.warning(f"Calculated quantity for {symbol} is <= 0")
                return

            portfolio = self.bot.portfolio.copy()
            portfolio["USD"] = cash - qty_usd
            portfolio[symbol] = portfolio.get(symbol, 0) + quantity

            self.bot.portfolio = portfolio
            self.bot_repository.update_bot(self.bot, session=sess)
            self.bot_repository.log_trade(
                bot_name=self.bot_name,
                symbol=symbol,
                quantity=quantity,
                price=price,
                is_buy=True,
                session=sess,
            )
            logger.info(
                "BOUGHT %.6f of %s at %.4f for cost %.2f", quantity, symbol, price, qty_usd
            )

        if session:
            _execute_buy(session)
        else:
            with get_db_session() as sess:
                _execute_buy(sess)
    
    def sell(
        self,
        symbol: str,
        quantity_usd: float = -1,
        cached_data: Optional[pd.DataFrame] = None,
        refresh: bool = True,
        session: Optional[Session] = None,
    ) -> None:
        """
        Sell a quantity of the specified symbol.
        
        Args:
            symbol: Trading symbol to sell
            quantity_usd: Amount in USD to sell (-1 means sell all holdings)
            cached_data: Optional cached DataFrame for price lookup
            refresh: Whether to refresh the bot from DB before executing
            session: Optional existing database session
        """
        def _execute_sell(sess: Session):
            if sess:
                # Lock row if in transaction
                self.bot = self.bot_repository.get_bot_locked(sess, self.bot_name)
            elif refresh:
                self._refresh_bot()

            holding = self.bot.portfolio.get(symbol, 0)
            if holding <= 0:
                logger.warning(f"No holdings of {symbol} to sell")
                return

            current_price = self.data_service.get_latest_price(symbol, cached_data)

            if quantity_usd == -1:
                quantity = holding
                qty_usd = quantity * current_price
            else:
                quantity = quantity_usd / current_price
                qty_usd = quantity_usd

            if quantity > holding:
                logger.warning(f"Insufficient holdings of {symbol} to sell requested amount. Selling all.")
                quantity = holding
                qty_usd = quantity * current_price

            if quantity <= 0:
                return

            portfolio = self.bot.portfolio.copy()
            portfolio["USD"] = portfolio.get("USD", 0) + qty_usd
            portfolio[symbol] = holding - quantity

            # Remove zero holdings
            if portfolio[symbol] <= 0.000001:
                del portfolio[symbol]

            self.bot.portfolio = portfolio
            self.bot_repository.update_bot(self.bot, session=sess)
            self.bot_repository.log_trade(
                bot_name=self.bot_name,
                symbol=symbol,
                quantity=quantity,
                price=current_price,
                is_buy=False,
                profit=qty_usd,  # Simplification: total proceeds as profit for now
                session=sess,
            )
            logger.info(
                "SOLD %.6f of %s at %.4f for proceeds %.2f",
                quantity,
                symbol,
                current_price,
                qty_usd,
            )

        if session:
            _execute_sell(session)
        else:
            with get_db_session() as sess:
                _execute_sell(sess)
    
    def rebalance_portfolio(self, target_portfolio: dict[str, float], only_over_50_usd: bool = False) -> None:
        """
        Rebalance portfolio to match target weights in a single transaction with row locking.
        
        Args:
            target_portfolio: Dictionary mapping symbols to target weights (e.g., {"VWCE": 0.8, "GLD": 0.1, "USD": 0.1})
                           Weights must sum to 1.0 (100%)
            only_over_50_usd: If True, filter out assets with target value <= $50
        """
        # Step 1: Validate weights sum to 1.0
        total_weight = sum(target_portfolio.values())
        if abs(total_weight - 1.0) > 0.01:
            raise ValueError(f"Target portfolio weights must sum to 1.0, got {total_weight}")

        with get_db_session() as session:
            # Lock bot row for the entire duration of rebalance
            self.bot = self.bot_repository.get_bot_locked(session, self.bot_name)
            
            # Step 2: Calculate current portfolio value
            current_usd = self.bot.portfolio.get("USD", 0)
            
            # Get all symbols involved
            all_involved_symbols = list(set(list(target_portfolio.keys()) + list(self.bot.portfolio.keys())))
            all_involved_symbols = [s for s in all_involved_symbols if s != "USD"]
            
            # Batch fetch prices
            prices = self.data_service.get_latest_prices_batch(all_involved_symbols)
            
            # Calculate total portfolio value
            total_portfolio_value = current_usd
            current_values = {"USD": current_usd}
            
            for symbol in all_involved_symbols:
                qty = self.bot.portfolio.get(symbol, 0)
                if qty > 0:
                    price = prices.get(symbol)
                    if price:
                        val = qty * price
                        current_values[symbol] = val
                        total_portfolio_value += val
                    else:
                        logger.warning(f"Could not get price for {symbol}, assuming zero value")
                        current_values[symbol] = 0
            
            if total_portfolio_value <= 0:
                logger.warning("Portfolio worth is zero, cannot rebalance")
                return

            # Step 3: Apply $50 threshold if requested
            actual_targets = target_portfolio.copy()
            if only_over_50_usd:
                filtered_weights = {}
                excluded_weight = 0
                
                for sym, weight in actual_targets.items():
                    if sym == "USD" or (weight * total_portfolio_value) > PORTFOLIO_CONFIG.min_asset_value_usd:
                        filtered_weights[sym] = weight
                    else:
                        excluded_weight += weight
                
                if excluded_weight > 0:
                    # Redistribute to remaining non-USD assets
                    non_usd_remaining = [s for s in filtered_weights if s != "USD"]
                    if non_usd_remaining:
                        redist_per_asset = excluded_weight / len(non_usd_remaining)
                        for s in non_usd_remaining:
                            filtered_weights[s] += redist_per_asset
                        actual_targets = filtered_weights
                    else:
                        # Put all in USD if no assets left
                        actual_targets = {"USD": 1.0}

            # Step 4: Calculate target values and differences
            target_values = {s: total_portfolio_value * w for s, w in actual_targets.items()}
            
            trades_to_sell = {} # symbol -> USD amount
            trades_to_buy = {}
            
            for symbol in all_involved_symbols:
                target_val = target_values.get(symbol, 0)
                current_val = current_values.get(symbol, 0)
                diff = target_val - current_val
                
                if diff < -1.0: # Sell
                    trades_to_sell[symbol] = abs(diff)
                elif diff > 1.0: # Buy
                    trades_to_buy[symbol] = diff

            logger.info(f"Rebalancing {self.bot_name}: Total Value ${total_portfolio_value:.2f}, {len(trades_to_sell)} sells, {len(trades_to_buy)} buys")

            # Step 5: Execute trades (Sells first)
            for symbol, usd_amt in trades_to_sell.items():
                self.sell(symbol, quantity_usd=usd_amt, refresh=False, session=session)
            
            # Re-read cash after sells
            for symbol, usd_amt in trades_to_buy.items():
                self.buy(symbol, quantity_usd=usd_amt, refresh=False, session=session)
            
            logger.info("Rebalance complete")


