"""Portfolio management for trading operations."""

from typing import Optional

import pandas as pd

from .bot_repository import BotRepository
from .constants import MIN_ASSET_VALUE_USD
from .data_service import DataService
from .db import Bot as BotModel


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

    def _refresh_bot(self) -> None:
        """Ensure the Bot instance is attached to an active session."""
        self.bot = self.bot_repository.create_or_get_bot(self.bot_name)
    
    def buy(self, symbol: str, quantity_usd: float = -1, cached_data: Optional[pd.DataFrame] = None) -> None:
        """
        Buy a quantity of the specified symbol.
        
        Args:
            symbol: Trading symbol to buy
            quantity_usd: Amount in USD to spend (-1 means use all available cash)
            cached_data: Optional cached DataFrame for price lookup
        """
        self._refresh_bot()
        cash = self.bot.portfolio.get("USD", 0)
        if quantity_usd == -1:
            # make it all the cash we have
            quantity_usd = cash
        assert cash >= quantity_usd, f"Not enough cash to buy: have ${cash:.2f}, need ${quantity_usd:.2f}"
        assert quantity_usd > 0, "Quantity USD must be greater than 0"
        price = self.data_service.get_latest_price(symbol, cached_data)
        quantity = quantity_usd / price
        assert quantity > 0, "Quantity must be greater than 0"
        cash -= quantity_usd
        self.bot.portfolio["USD"] = cash
        self.bot.portfolio[symbol] = self.bot.portfolio.get(symbol, 0) + quantity
        self.bot_repository.update_bot(self.bot)
        self.bot_repository.log_trade(
            bot_name=self.bot_name,
            symbol=symbol,
            quantity=quantity,
            price=price,
            is_buy=True,
        )
        print(
            f"Buying {quantity} of {symbol} at {price} for cost {quantity_usd}"
        )
    
    def sell(self, symbol: str, quantity_usd: float = -1, cached_data: Optional[pd.DataFrame] = None) -> None:
        """
        Sell a quantity of the specified symbol.
        
        Args:
            symbol: Trading symbol to sell
            quantity_usd: Amount in USD to sell (-1 means sell all holdings)
            cached_data: Optional cached DataFrame for price lookup
        """
        self._refresh_bot()
        cash = self.bot.portfolio.get("USD", 0)
        holding = self.bot.portfolio.get(symbol, 0)
        current_price = self.data_service.get_latest_price(symbol, cached_data)
        
        if quantity_usd == -1:
            # make it all the stock we have
            quantity = holding
            # Calculate actual USD value for logging
            quantity_usd = quantity * current_price
        else:
            quantity = quantity_usd / current_price
        
        assert quantity <= holding, "Not enough stock to sell"
        assert quantity > 0, "Quantity must be greater than 0"

        cash = self.bot.portfolio.get("USD", 0)
        profit = quantity * current_price
        cash += profit
        self.bot.portfolio["USD"] = cash
        self.bot.portfolio[symbol] -= quantity
        self.bot_repository.update_bot(self.bot)
        self.bot_repository.log_trade(
            bot_name=self.bot_name,
            symbol=symbol,
            quantity=quantity,
            price=current_price,
            is_buy=False,
            profit=profit,
        )
        print(
            f"Selling {quantity} of {symbol} at {current_price} for profit {profit}"
        )
    
    def rebalance_portfolio(self, target_portfolio: dict[str, float], only_over_50_usd: bool = False) -> None:
        """
        Rebalance portfolio to match target weights.
        
        Args:
            target_portfolio: Dictionary mapping symbols to target weights (e.g., {"VWCE": 0.8, "GLD": 0.1, "USD": 0.1})
                           Weights must sum to 1.0 (100%)
            only_over_50_usd: If True, filter out assets with target value <= $50 and redistribute weights equally
                          among remaining assets (default: False)
        
        Raises:
            ValueError: If weights don't sum to 1.0 (within tolerance)
        """
        self._refresh_bot()
        # Step 1: Validate weights sum to 1.0
        total_weight = sum(target_portfolio.values())
        if abs(total_weight - 1.0) > 0.001:
            raise ValueError(
                f"Target portfolio weights must sum to 1.0, got {total_weight}"
            )
        
        # Step 2: Calculate current portfolio value
        current_usd = self.bot.portfolio.get("USD", 0)
        total_portfolio_value = current_usd
        
        # Get all symbols that need prices (excluding USD)
        symbols_to_price = [
            symbol for symbol, quantity in self.bot.portfolio.items()
            if symbol != "USD" and quantity > 0
        ]
        
        # Batch fetch prices
        prices = self.data_service.get_latest_prices_batch(symbols_to_price)
        
        # Calculate value of all non-USD assets
        current_values = {}
        for symbol, quantity in self.bot.portfolio.items():
            if symbol == "USD":
                continue
            if quantity > 0:
                if symbol in prices:
                    price = prices[symbol]
                    value = quantity * price
                    current_values[symbol] = value
                    total_portfolio_value += value
                else:
                    print(f"Warning: Could not get price for {symbol}, skipping")
                    # Skip this asset for rebalancing
                    continue
        
        # Add USD to current_values for easier calculation
        current_values["USD"] = current_usd
        
        # Step 2.5: Filter out assets with target value <= $50 if requested
        if only_over_50_usd:
            print(f"Total portfolio value: ${total_portfolio_value:,.2f}")
            
            # Get prices for symbols in targetPortfolio that might not be in current portfolio
            target_symbols_to_price = [
                symbol for symbol in target_portfolio.keys()
                if symbol != "USD" and symbol not in prices
            ]
            if target_symbols_to_price:
                additional_prices = self.data_service.get_latest_prices_batch(target_symbols_to_price)
                prices.update(additional_prices)
            
            filtered_weights = {}
            excluded_weights = {}
            
            for symbol, weight in target_portfolio.items():
                if symbol == "USD":
                    # Keep USD weight as-is
                    filtered_weights[symbol] = weight
                    continue
                
                target_value = total_portfolio_value * weight
                if target_value > MIN_ASSET_VALUE_USD:
                    filtered_weights[symbol] = weight
                else:
                    excluded_weights[symbol] = weight
            
            if excluded_weights:
                excluded_total_weight = sum(excluded_weights.values())
                print(f"Excluding {len(excluded_weights)} assets with target value <= ${MIN_ASSET_VALUE_USD}: {list(excluded_weights.keys())}")
                print(f"Total excluded weight: {excluded_total_weight:.2%}")
                
                # Get non-USD assets for redistribution
                non_usd_filtered = {k: v for k, v in filtered_weights.items() if k != "USD"}
                
                if non_usd_filtered:
                    # Redistribute excluded weights equally among remaining non-USD assets
                    weight_per_asset = excluded_total_weight / len(non_usd_filtered)
                    for symbol in non_usd_filtered:
                        filtered_weights[symbol] += weight_per_asset
                    
                    # Renormalize to ensure weights sum to 1.0
                    total_filtered_weight = sum(filtered_weights.values())
                    if total_filtered_weight > 0:
                        target_portfolio = {k: v / total_filtered_weight for k, v in filtered_weights.items()}
                    else:
                        raise ValueError("All weights were excluded, cannot rebalance")
                else:
                    raise ValueError("All assets were excluded (all target values <= $50), cannot rebalance")
            
            print(f"After filtering, portfolio contains {len([k for k in target_portfolio.keys() if k != 'USD'])} assets")
        
        # Step 3: Calculate target values
        target_values = {}
        for symbol, weight in target_portfolio.items():
            target_values[symbol] = total_portfolio_value * weight
        
        # Step 4: Calculate differences and determine trades
        # For assets in current portfolio (including those not in target)
        trades_to_sell = {}  # symbol -> USD amount to sell
        trades_to_buy = {}   # symbol -> USD amount to buy
        
        # Check all assets in current portfolio
        for symbol in self.bot.portfolio.keys():
            if symbol == "USD":
                continue
            current_value = current_values.get(symbol, 0)
            target_value = target_values.get(symbol, 0)
            difference = current_value - target_value
            
            if difference > 0.01:  # Small threshold to avoid tiny trades
                trades_to_sell[symbol] = difference
            elif difference < -0.01:
                trades_to_buy[symbol] = abs(difference)
        
        # Check assets in target that aren't in current portfolio
        for symbol, target_value in target_values.items():
            if symbol == "USD":
                continue
            if symbol not in self.bot.portfolio:
                if target_value > 0.01:  # Only buy if target is meaningful
                    trades_to_buy[symbol] = target_value
        
        # Step 5: Execute trades (sells first, then buys)
        # Calculate total needed cash for buys
        total_buy_needed = sum(trades_to_buy.values())
        total_sell_expected = sum(trades_to_sell.values())
        initial_cash = self.bot.portfolio.get("USD", 0)
        
        # If we need to buy but don't have enough cash, we need to sell more
        # This can happen if portfolio is fully invested and target includes new assets
        if total_buy_needed > (initial_cash + total_sell_expected):
            cash_shortfall = total_buy_needed - (initial_cash + total_sell_expected)
            if total_sell_expected > 0:
                print(f"Warning: Need ${cash_shortfall:.2f} more cash. Will sell proportionally more from over-weighted positions.")
                # Scale up sells proportionally to cover the shortfall
                scale_factor = (total_buy_needed - initial_cash) / total_sell_expected
                trades_to_sell = {symbol: amount * scale_factor for symbol, amount in trades_to_sell.items()}
            else:
                # No sells possible, but we need cash - this means portfolio is fully invested
                # and target includes new assets. We can't rebalance without selling existing assets.
                print(f"Warning: Need ${cash_shortfall:.2f} to buy new assets, but no over-weighted positions to sell. Skipping buys.")
                trades_to_buy = {}  # Clear buys since we can't afford them
        
        # Sell phase - execute all sells first to free up cash
        for symbol, usd_amount in trades_to_sell.items():
            try:
                self.sell(symbol, quantity_usd=usd_amount)
            except Exception as e:
                print(f"Error selling {symbol}: {e}")
                # Continue with other trades
        
        # Refresh bot after all sells to get latest cash amount
        self._refresh_bot()
        
        # Buy phase - only buy if we have cash available
        for symbol, usd_amount in trades_to_buy.items():
            # Refresh bot before each buy check to get latest cash after previous buys
            self._refresh_bot()
            available_cash = self.bot.portfolio.get("USD", 0)
            if available_cash <= 0:
                print(f"Skipping all remaining buys: no cash available (${available_cash:.2f})")
                break
            
            if available_cash < usd_amount:
                print(f"Skipping buy of {symbol}: insufficient cash (available: ${available_cash:.2f}, needed: ${usd_amount:.2f})")
                continue
            
            try:
                self.buy(symbol, quantity_usd=usd_amount)
            except AssertionError as e:
                # AssertionError means the buy method's internal check failed
                print(f"Error buying {symbol}: {e}")
                continue
            except Exception as e:
                print(f"Error buying {symbol}: {e}")
                # Continue with other trades
        
        # USD will naturally be correct after all trades execute
        # (since trades were calculated to achieve target weights)
        # Recalculate final portfolio value for reporting
        final_usd = self.bot.portfolio.get("USD", 0)
        final_total_value = final_usd
        
        # Batch fetch prices for final calculation
        final_symbols_to_price = [
            symbol for symbol, quantity in self.bot.portfolio.items()
            if symbol != "USD" and quantity > 0
        ]
        final_prices = self.data_service.get_latest_prices_batch(final_symbols_to_price)
        
        for symbol, quantity in self.bot.portfolio.items():
            if symbol == "USD":
                continue
            if quantity > 0:
                if symbol in final_prices:
                    price = final_prices[symbol]
                    final_total_value += quantity * price
                else:
                    print(f"Warning: Could not get price for {symbol} during final calculation")
        
        target_usd_weight = target_portfolio.get("USD", 0)
        target_usd = final_total_value * target_usd_weight
        actual_usd_weight = final_usd / final_total_value if final_total_value > 0 else 0
        
        self.bot_repository.update_bot(self.bot)
        print(f"Portfolio rebalanced. Final USD: {final_usd:.2f} "
              f"(target: {target_usd:.2f}, actual weight: {actual_usd_weight:.1%}, target weight: {target_usd_weight:.1%})")

