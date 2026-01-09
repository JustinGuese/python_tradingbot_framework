"""Helper functions for calculating and retrieving portfolio worth."""

import math
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from .data_service import DataService
from .db import Bot as BotModel
from .db import PortfolioWorth, get_db_session
from .helpers import ensure_utc_series, ensure_utc_timestamp


def calculate_portfolio_worth(
    bot: BotModel,
    data_service: DataService,
    date: Optional[datetime] = None,
) -> float:
    """
    Calculate portfolio worth for a bot at a specific date (or current).
    
    Args:
        bot: BotModel instance
        data_service: DataService instance for fetching prices
        date: Optional date to calculate worth at (uses historic prices if provided)
        
    Returns:
        Total portfolio worth in USD
    """
    portfolio = bot.portfolio
    cash = portfolio.get("USD", 0)
    
    # Get all non-USD holdings
    holdings = {symbol: quantity for symbol, quantity in portfolio.items() if symbol != "USD" and quantity > 0}
    
    if not holdings:
        return cash
    
    # If date is provided, use historic prices; otherwise use latest prices
    if date:
        # Use historic prices from database
        total_value = cash
        for symbol, quantity in holdings.items():
            # Get price at or before the specified date
            historic_data = data_service.get_data_from_db(
                symbol=symbol,
                start_date=None,
                end_date=ensure_utc_timestamp(pd.Timestamp(date)),
            )
            if not historic_data.empty:
                # Get the latest price before or at the date
                price = historic_data.iloc[-1]["close"]
                total_value += quantity * price
            else:
                # If no historic data, skip this holding (or use 0)
                print(f"Warning: No historic data for {symbol} at {date}")
        return total_value
    else:
        # Use latest prices
        symbols = list(holdings.keys())
        prices = data_service.get_latest_prices_batch(symbols)
        
        total_value = cash
        for symbol, quantity in holdings.items():
            if symbol in prices:
                total_value += quantity * prices[symbol]
            else:
                print(f"Warning: Could not get price for {symbol}")
        
        return total_value


def get_portfolio_worth_history(bot_name: str) -> pd.DataFrame:
    """
    Retrieve portfolio worth time series from database.
    
    Args:
        bot_name: Name of the bot
        
    Returns:
        DataFrame with columns: date, portfolio_worth, holdings
        Empty DataFrame if no data found
    """
    with get_db_session() as session:
        results = (
            session.query(PortfolioWorth)
            .filter_by(bot_name=bot_name)
            .order_by(PortfolioWorth.date)
            .all()
        )
        
        if not results:
            return pd.DataFrame()
        
        data = pd.DataFrame([{
            "date": r.date,
            "portfolio_worth": r.portfolio_worth,
            "holdings": r.holdings,
        } for r in results])
        
        # Ensure date is timezone-aware (UTC)
        if "date" in data.columns:
            data["date"] = pd.to_datetime(data["date"])
            data["date"] = ensure_utc_series(data["date"])
        
        return data


def calculate_performance_metrics(worth_series: pd.Series) -> dict:
    """
    Calculate performance metrics using quantstats.
    
    Args:
        worth_series: Series with portfolio worth over time (index should be dates)
        
    Returns:
        Dictionary with performance metrics
    """
    try:
        import quantstats as qs
    except ImportError:
        return {
            "error": "quantstats not installed",
            "total_return": None,
            "annualized_return": None,
            "sharpe_ratio": None,
            "max_drawdown": None,
        }
    
    if len(worth_series) < 2:
        return {
            "total_return": 0.0,
            "annualized_return": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown": 0.0,
        }
    
    # Calculate daily returns
    returns = worth_series.pct_change().dropna()
    
    if len(returns) == 0:
        return {
            "total_return": 0.0,
            "annualized_return": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown": 0.0,
        }
    
    # Calculate metrics
    total_return = (worth_series.iloc[-1] / worth_series.iloc[0] - 1) * 100
    
    # Annualized return
    days = (worth_series.index[-1] - worth_series.index[0]).days
    if days > 0:
        annualized_return = ((worth_series.iloc[-1] / worth_series.iloc[0]) ** (365.25 / days) - 1) * 100
    else:
        annualized_return = 0.0
    
    # Sharpe ratio (assuming risk-free rate of 0)
    sharpe_ratio = qs.stats.sharpe(returns, periods=252) if len(returns) > 1 else 0.0
    
    # Max drawdown
    max_drawdown = qs.stats.max_drawdown(returns) * 100 if len(returns) > 1 else 0.0
    
    # Additional metrics
    sortino_ratio = qs.stats.sortino(returns, periods=252) if len(returns) > 1 else 0.0
    calmar_ratio = qs.stats.calmar(returns) if len(returns) > 1 else 0.0
    volatility = qs.stats.volatility(returns, periods=252) * 100 if len(returns) > 1 else 0.0
    
    # Helper function to clean NaN/inf values
    def clean_value(val):
        if val is None:
            return 0.0
        if isinstance(val, (np.floating, np.integer)):
            if np.isnan(val) or np.isinf(val):
                return 0.0
            return float(val)
        if isinstance(val, (float, int)):
            if math.isnan(val) or math.isinf(val):
                return 0.0
            return float(val)
        return 0.0
    
    return {
        "total_return": round(clean_value(total_return), 2),
        "annualized_return": round(clean_value(annualized_return), 2),
        "sharpe_ratio": round(clean_value(sharpe_ratio), 2),
        "sortino_ratio": round(clean_value(sortino_ratio), 2),
        "calmar_ratio": round(clean_value(calmar_ratio), 2),
        "max_drawdown": round(clean_value(max_drawdown), 2),
        "volatility": round(clean_value(volatility), 2),
    }

