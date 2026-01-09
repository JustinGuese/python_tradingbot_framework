"""Backtesting functionality for trading bots."""

from typing import Optional

import numpy as np
import pandas as pd

from .botclass import Bot


def _get_periods_per_year(interval: str) -> float:
    """
    Calculate approximate number of periods per trading year for a given interval.
    
    Args:
        interval: Data interval string (e.g., "1d", "1h", "1m")
    
    Returns:
        Approximate number of periods per trading year
    """
    # Trading year assumptions:
    # - 252 trading days per year
    # - ~6.5 trading hours per day (9:30 AM - 4:00 PM ET)
    # - ~390 trading minutes per day (6.5 hours * 60 minutes)
    
    if interval == "1d":
        return 252.0
    elif interval == "1wk":
        return 52.0
    elif interval == "1mo":
        return 12.0
    elif interval in ["1h", "60m"]:
        return 252.0 * 6.5  # ~1,638 periods per year
    elif interval == "4h":
        return 252.0 * 1.625  # ~409.5 periods per year
    elif interval == "1m":
        return 252.0 * 390  # ~98,280 periods per year
    elif interval == "5m":
        return 252.0 * 78  # ~19,656 periods per year
    elif interval == "15m":
        return 252.0 * 26  # ~6,552 periods per year
    elif interval == "30m":
        return 252.0 * 13  # ~3,276 periods per year
    else:
        # Default: assume daily frequency
        return 252.0


def _get_backtest_period(interval: str) -> str:
    """
    Get appropriate backtest period based on interval, respecting Yahoo Finance limits.
    
    Yahoo Finance limits:
    - 1m, 2m, 5m, 15m, 30m, 60m, 90m: max 60 days
    - 1h: max 730 days (2 years)
    - 1d, 5d, 1wk, 1mo, 3mo: max available (years)
    
    Args:
        interval: Data interval string (e.g., "1d", "1h", "1m")
    
    Returns:
        Period string suitable for backtesting (e.g., "7d", "60d", "1y")
    """
    # For minute-level data, Yahoo Finance limits to 60 days, but we use 7d to be safe
    if interval in ["1m", "2m", "5m", "15m", "30m", "60m", "90m"]:
        return "7d"  # Safe limit for minute data
    elif interval in ["1h", "60m"]:
        return "60d"  # 60 days for hourly data
    elif interval in ["1d", "5d", "1wk", "1mo", "3mo"]:
        return "1y"  # 1 year for daily/weekly/monthly data
    else:
        # Default: use 1 year for unknown intervals
        return "1y"


def backtest_bot(
    bot: Bot,
    initial_capital: float = 10000.0,
    save_to_db: bool = True,
    data: Optional[pd.DataFrame] = None,
) -> dict:
    """
    Backtest a trading bot over the last year's data.
    
    Args:
        bot: Bot instance to backtest (must have decisionFunction implemented)
        initial_capital: Starting capital in USD (default: $10,000)
        save_to_db: Whether to save fetched data to database (default: True).
                    Set to True to enable data reuse across multiple backtests.
        data: Optional pre-fetched DataFrame with technical indicators.
              If provided, skips data fetching and uses this data directly.
              Must have columns: timestamp, close, and all required TA indicators.
    
    Returns:
        Dictionary with keys:
        - yearly_return: Annualized return as decimal (e.g., 0.15 for 15%)
        - sharpe_ratio: Sharpe ratio (annualized, assuming 252 trading days)
        - nrtrades: Total number of trades executed (buy + sell)
        - maxdrawdown: Maximum drawdown as decimal (e.g., 0.25 for 25%)
    
    Raises:
        NotImplementedError: If bot doesn't implement decisionFunction
        ValueError: If insufficient data is available for backtesting
    """
    # Check if bot implements decisionFunction
    if not hasattr(bot, 'decisionFunction') or bot.decisionFunction == Bot.decisionFunction:
        raise NotImplementedError(
            "Bot must implement decisionFunction() method for backtesting. "
            "Bots that only override makeOneIteration() are not supported."
        )
    
    # Check if bot has a symbol (required for single-asset bots)
    if bot.symbol is None:
        raise ValueError(
            "Bot must have a symbol defined for backtesting. "
            "Multi-asset bots are not currently supported."
        )
    
    # Use provided data or fetch historical data with technical indicators
    backtest_period = None
    if data is not None:
        # Validate that provided data has required columns
        if 'close' not in data.columns or 'timestamp' not in data.columns:
            raise ValueError(
                "Provided data must have 'close' and 'timestamp' columns. "
                "It should also include all technical indicators required by the bot's decisionFunction."
            )
    else:
        # Determine appropriate period based on interval (respects Yahoo Finance limits)
        backtest_period = _get_backtest_period(bot.interval)
        try:
            data = bot.getYFDataWithTA(
                interval=bot.interval,
                period=backtest_period,
                saveToDB=save_to_db
            )
        except Exception as e:
            raise ValueError(f"Failed to fetch historical data: {e}")
    
    # Validate data
    if data.empty:
        raise ValueError("No historical data available for backtesting")
    
    if len(data) < 2:
        raise ValueError("Insufficient data points for backtesting (need at least 2)")
    
    # Ensure data is sorted chronologically
    if 'timestamp' in data.columns:
        data = data.sort_values('timestamp').reset_index(drop=True)
    elif data.index.name in ['timestamp', 'date', 'datetime']:
        data = data.sort_index()
    
    # Set bot.data so decisionFunction can access it (for bots that need full DataFrame context)
    bot.data = data
    if backtest_period:
        bot.datasettings = (bot.interval, backtest_period)
    
    # Initialize simulation state
    portfolio = {"USD": initial_capital}
    symbol = bot.symbol
    portfolio_values = []
    nrtrades = 0
    peak_value = initial_capital
    
    # Iterate through historical data chronologically
    for idx, row in data.iterrows():
        # Get current price from row
        try:
            current_price = float(row['close'])
            if current_price <= 0 or not np.isfinite(current_price):
                # Skip invalid price data
                continue
        except (KeyError, ValueError, TypeError):
            # Skip rows without valid price data
            continue
        
        # Get trading decision
        try:
            decision = bot.decisionFunction(row)
        except Exception as e:
            # Skip rows that cause errors in decision function
            print(f"Warning: Error in decisionFunction at row {idx}: {e}")
            decision = 0
        
        # Execute simulated buy/sell
        cash = portfolio.get("USD", 0.0)
        holdings = portfolio.get(symbol, 0.0)
        
        if decision == 1:  # Buy signal
            if cash > 0:
                # Buy with all available cash
                quantity = cash / current_price
                portfolio["USD"] = 0.0
                portfolio[symbol] = holdings + quantity
                nrtrades += 1
        
        elif decision == -1:  # Sell signal
            if holdings > 0:
                # Sell all holdings
                cash_proceeds = holdings * current_price
                portfolio["USD"] = cash + cash_proceeds
                portfolio[symbol] = 0.0
                nrtrades += 1
        
        # Calculate current portfolio value
        current_cash = portfolio.get("USD", 0.0)
        current_holdings = portfolio.get(symbol, 0.0)
        portfolio_value = current_cash + (current_holdings * current_price)
        
        # Track portfolio value
        portfolio_values.append(portfolio_value)
        
        # Update peak value for drawdown calculation
        if portfolio_value > peak_value:
            peak_value = portfolio_value
    
    # Validate we have enough data points for calculations
    if len(portfolio_values) < 2:
        raise ValueError("Insufficient portfolio value data for metrics calculation")
    
    # Calculate metrics
    final_value = portfolio_values[-1]
    initial_value = portfolio_values[0]
    
    # Yearly Return
    if initial_value > 0:
        yearly_return = (final_value - initial_value) / initial_value
    else:
        yearly_return = 0.0
    
    # Sharpe Ratio
    # Calculate period returns (returns at the data frequency)
    portfolio_series = pd.Series(portfolio_values)
    period_returns = portfolio_series.pct_change().dropna()
    
    if len(period_returns) == 0:
        sharpe_ratio = 0.0
    else:
        mean_return = period_returns.mean()
        std_return = period_returns.std()
        
        if std_return == 0 or not np.isfinite(std_return):
            sharpe_ratio = 0.0
        else:
            # Calculate periods per year based on interval
            # This maps the data interval to approximate periods per trading year
            periods_per_year = _get_periods_per_year(bot.interval)
            
            # Annualize returns and volatility
            # Mean return: multiply by periods per year
            # Volatility: multiply by sqrt(periods per year) due to independence assumption
            annualized_return = mean_return * periods_per_year
            annualized_vol = std_return * np.sqrt(periods_per_year)
            
            if annualized_vol == 0:
                sharpe_ratio = 0.0
            else:
                # Sharpe ratio = annualized return / annualized volatility
                # Assuming risk-free rate is 0 for simplicity
                sharpe_ratio = annualized_return / annualized_vol
    
    # Max Drawdown
    if len(portfolio_values) < 2:
        maxdrawdown = 0.0
    else:
        portfolio_array = np.array(portfolio_values)
        # Calculate running maximum (peak)
        running_max = np.maximum.accumulate(portfolio_array)
        # Calculate drawdown at each point
        drawdowns = (running_max - portfolio_array) / running_max
        # Get maximum drawdown
        maxdrawdown = float(np.max(drawdowns))
        
        # Handle edge cases
        if not np.isfinite(maxdrawdown):
            maxdrawdown = 0.0
    
    return {
        "yearly_return": float(yearly_return),
        "sharpe_ratio": float(sharpe_ratio),
        "nrtrades": int(nrtrades),
        "maxdrawdown": float(maxdrawdown),
    }
