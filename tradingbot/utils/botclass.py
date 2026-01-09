"""
Base Bot class for trading bot implementations.

This module provides the core Bot class that all trading bots inherit from.
It handles data fetching, trading operations, portfolio management, and database
interactions. Subclasses should implement either decisionFunction() for simple
strategies or makeOneIteration() for more complex logic.

Key Features:
- Automatic data fetching from Yahoo Finance with database caching
- Technical analysis indicator calculation
- Portfolio management (buy/sell/rebalance)
- Trade logging and run history
- Hyperparameter tuning and backtesting utilities

Example:
    class MyBot(Bot):
        def __init__(self):
            super().__init__("MyBot", "QQQ", interval="1m", period="1d")
        
        def decisionFunction(self, row):
            if row["momentum_rsi"] < 30:
                return 1  # Buy
            elif row["momentum_rsi"] > 70:
                return -1  # Sell
            return 0  # Hold
"""

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from .bot_repository import BotRepository
from .data_service import DataService
from .db import RunLog, get_db_session
from .portfolio_manager import PortfolioManager


class Bot:
    """
    Base class for trading bots.
    
    Provides common functionality for data fetching, trading operations,
    portfolio management, and database interactions.
    
    Data Caching:
    - Each Bot instance has its own DataService instance with per-instance caching.
    - For cross-run data reuse (e.g., hyperparameter tuning), data is persisted
      to the database when saveToDB=True. Subsequent fetches check the database
      first and only call yfinance if data is missing or stale.
    - Best practice: Use saveToDB=True for historical backtests to enable efficient
      data reuse across multiple runs or parameter combinations.
    
    Subclasses should implement either:
    - decisionFunction(row) -> int: Returns -1 (sell), 0 (hold), or 1 (buy)
    - makeOneIteration() -> int: Custom iteration logic
    """
    
    # Optional class attribute: subclasses can define their hyperparameter search space
    param_grid: Optional[Dict[str, List[Any]]] = None

    def __init__(self, name: str, symbol: Optional[str] = None, interval: str = "1m", period: str = "1d", **kwargs):
        """
        Initialize a trading bot.
        
        Args:
            name: Unique name for the bot (used for database identification)
            symbol: Trading symbol (e.g., "EURUSD=X", "^XAU", "QQQ")
                    Optional for multi-asset bots that override makeOneIteration()
            interval: Data interval (e.g., "1m", "5m", "1h", "1d") - default: "1m"
            period: Data period (e.g., "1d", "5d", "1mo", "1y") - default: "1d"
            **kwargs: Arbitrary hyperparameters that will be stored in self.params
                     and can be accessed by subclasses for flexible parameterization
        """
        self.bot_name = name  # Store name separately to avoid DetachedInstanceError
        self.dbBot = BotRepository.create_or_get_bot(name)
        self.symbol = symbol
        self.interval = interval
        self.period = period
        
        # Store hyperparameters in a dictionary for flexible access
        self.params = kwargs.copy() if kwargs else {}
        
        # Initialize services
        self._data_service = DataService()
        self._bot_repository = BotRepository()
        self._portfolio_manager = PortfolioManager(
            bot=self.dbBot,
            bot_name=self.bot_name,
            data_service=self._data_service,
            bot_repository=self._bot_repository,
        )
        
        # Maintain backward compatibility for data caching
        self.data: Optional[pd.DataFrame] = None
        self.datasettings: Tuple[Optional[str], Optional[str]] = (None, None)
    
    # Data fetching methods - delegate to DataService
    def _parsePeriodToDateRange(self, period: str) -> Tuple[pd.Timestamp, pd.Timestamp]:
        """
        Convert yfinance period string to start and end datetime range.
        
        Args:
            period: Period string (e.g., "1d", "5d", "1mo", "1y", "ytd", "max")
            
        Returns:
            Tuple of (start_date, end_date) in UTC timezone-aware timestamps
        """
        from .helpers import parse_period_to_date_range
        return parse_period_to_date_range(period)
    
    def getDataFromDB(
        self,
        symbol: str,
        start_date: Optional[pd.Timestamp] = None,
        end_date: Optional[pd.Timestamp] = None,
    ) -> pd.DataFrame:
        """
        Load data from database for a symbol.
        
        Args:
            symbol: Trading symbol to query
            start_date: Optional start date (timezone-aware UTC)
            end_date: Optional end date (timezone-aware UTC)
            
        Returns:
            DataFrame with columns: symbol, timestamp, open, high, low, close, volume
            Empty DataFrame if no data found
        """
        return self._data_service.get_data_from_db(symbol, start_date, end_date)
    
    def getYFData(
        self,
        symbol: Optional[str] = None,
        interval: str = "1m",
        period: str = "1d",
        saveToDB: bool = False,
    ) -> pd.DataFrame:
        """
        Fetch market data from Yahoo Finance, checking database first.
        
        Args:
            symbol: Trading symbol (defaults to self.symbol)
            interval: Data interval (e.g., "1m", "5m", "1h", "1d")
            period: Data period (e.g., "1d", "5d", "1mo", "1y")
            saveToDB: Whether to save fetched data to database
            
        Returns:
            DataFrame with columns: symbol, timestamp, open, high, low, close, volume
        """
        if not symbol:
            if self.symbol is None:
                raise ValueError("symbol parameter is required when self.symbol is None (multi-asset bot)")
            symbol = self.symbol
        
        data = self._data_service.get_yf_data(
            symbol=symbol,
            interval=interval,
            period=period,
            save_to_db=saveToDB,
            use_cache=True,
        )
        
        # Update cache for backward compatibility
        if (interval, period) == self.datasettings:
            self.data = data
        
        return data
    
    def getYFDataWithTA(
        self,
        symbol: Optional[str] = None,
        interval: str = "1m",
        period: str = "1d",
        saveToDB: bool = False,
    ) -> pd.DataFrame:
        """
        Fetch market data with technical analysis indicators.
        
        Data fetching strategy:
        - Checks database first for existing data
        - Only fetches from yfinance if data is missing or stale
        - If saveToDB=True, saves fetched data to database for future reuse
        
        Note: For repeated backtests or hyperparameter tuning, set saveToDB=True
        to enable efficient data reuse across multiple runs.
        
        Args:
            symbol: Trading symbol (defaults to self.symbol)
            interval: Data interval (e.g., "1m", "5m", "1h", "1d")
            period: Data period (e.g., "1d", "5d", "1mo", "1y")
            saveToDB: Whether to save fetched data to database. Set to True for
                     historical backtests to enable data reuse.
            
        Returns:
            DataFrame with market data and technical analysis features
        """
        if not symbol:
            if self.symbol is None:
                raise ValueError("symbol parameter is required when self.symbol is None (multi-asset bot)")
            symbol = self.symbol
        
        data = self._data_service.get_yf_data_with_ta(
            symbol=symbol,
            interval=interval,
            period=period,
            save_to_db=saveToDB,
        )
        
        # Update cache for backward compatibility
        if (interval, period) == self.datasettings:
            self.data = data
        
        return data
    
    def getYFDataMultiple(
        self,
        symbols: list[str],
        interval: str = "1d",
        period: str = "3mo",
        saveToDB: bool = True,
    ) -> pd.DataFrame:
        """
        Fetch market data for multiple symbols efficiently, checking database first.
        
        Args:
            symbols: List of trading symbols to fetch
            interval: Data interval (e.g., "1m", "5m", "1h", "1d")
            period: Data period (e.g., "1d", "5d", "1mo", "3mo", "1y")
            saveToDB: Whether to save fetched data to database for each symbol
            
        Returns:
            DataFrame with columns: symbol, timestamp, open, high, low, close, volume
            Combined data from all symbols in long format
        """
        return self._data_service.get_yf_data_multiple(
            symbols=symbols,
            interval=interval,
            period=period,
            save_to_db=saveToDB,
        )
    
    def convertToWideFormat(
        self,
        data_long: pd.DataFrame,
        value_column: str = "close",
        fill_method: str = "both",
    ) -> pd.DataFrame:
        """
        Convert long-format DataFrame to wide format for portfolio optimization.
        
        Args:
            data_long: DataFrame in long format with columns: symbol, timestamp, open, high, low, close, volume
            value_column: Column name to use as values (default: "close")
            fill_method: How to handle missing values - "forward", "backward", "both", or None
            
        Returns:
            DataFrame with timestamp as index, symbols as columns, and specified value column as values
        """
        return self._data_service.convert_to_wide_format(
            data_long=data_long,
            value_column=value_column,
            fill_method=fill_method,
        )
    
    def addPdDFToDb(self, df: pd.DataFrame) -> None:
        """
        Add DataFrame rows to database, skipping duplicates.
        
        Only inserts rows with timestamps newer than the latest in database.
        
        Args:
            df: DataFrame with columns: symbol, timestamp, open, high, low, close, volume
        """
        self._data_service.add_pd_df_to_db(df)
    
    def getLatestPrice(self, symbol: str) -> float:
        """
        Get the latest price for a symbol, using TTL cache and checking DB first.
        
        Args:
            symbol: Trading symbol to get price for
            
        Returns:
            Latest price as float
            
        Raises:
            ValueError: If no price data is available
        """
        return self._data_service.get_latest_price(symbol, cached_data=self.data)
    
    def getLatestPricesBatch(self, symbols: list[str]) -> dict[str, float]:
        """
        Get latest prices for multiple symbols in a single DB query.
        
        Args:
            symbols: List of trading symbols to get prices for
            
        Returns:
            Dictionary mapping symbol to latest price
        """
        return self._data_service.get_latest_prices_batch(symbols)
    
    # Portfolio management methods - delegate to PortfolioManager
    def buy(self, symbol: str, quantityUSD: float = -1) -> None:
        """
        Buy a quantity of the specified symbol.
        
        Args:
            symbol: Trading symbol to buy
            quantityUSD: Amount in USD to spend (-1 means use all available cash)
        """
        self._portfolio_manager.buy(symbol, quantity_usd=quantityUSD, cached_data=self.data)
        # Refresh dbBot reference after portfolio update
        self.dbBot = self._bot_repository.create_or_get_bot(self.bot_name)
    
    def sell(self, symbol: str, quantityUSD: float = -1) -> None:
        """
        Sell a quantity of the specified symbol.
        
        Args:
            symbol: Trading symbol to sell
            quantityUSD: Amount in USD to sell (-1 means sell all holdings)
        """
        self._portfolio_manager.sell(symbol, quantity_usd=quantityUSD, cached_data=self.data)
        # Refresh dbBot reference after portfolio update
        self.dbBot = self._bot_repository.create_or_get_bot(self.bot_name)
    
    def rebalancePortfolio(self, targetPortfolio: dict[str, float], onlyOver50USD: bool = False) -> None:
        """
        Rebalance portfolio to match target weights.
        
        Args:
            targetPortfolio: Dictionary mapping symbols to target weights (e.g., {"VWCE": 0.8, "GLD": 0.1, "USD": 0.1})
                           Weights must sum to 1.0 (100%)
            onlyOver50USD: If True, filter out assets with target value <= $50 and redistribute weights equally
                          among remaining assets (default: False)
        
        Raises:
            ValueError: If weights don't sum to 1.0 (within tolerance)
        """
        self._portfolio_manager.rebalance_portfolio(targetPortfolio, only_over_50_usd=onlyOver50USD)
        # Refresh dbBot reference after portfolio update
        self.dbBot = self._bot_repository.create_or_get_bot(self.bot_name)
    
    # Decision and execution methods
    def decisionFunction(self, row: pd.Series) -> int:
        """
        Decision function that determines trading action based on market data row.
        
        **Must be overridden by subclasses** (unless using makeOneIteration() instead).
        
        This is the preferred approach for most bots. The base class will:
        1. Apply this function to each row in the DataFrame
        2. Average the last N decisions (default: 1)
        3. Execute trades based on the final decision
        
        Args:
            row: Pandas Series containing:
                - Market data: symbol, timestamp, open, high, low, close, volume
                - Technical indicators: ~150+ indicators (e.g., momentum_rsi, trend_macd, etc.)
                - Access via: row["indicator_name"]
        
        Returns:
            -1: Sell signal (will sell holdings if any exist)
             0: Hold (no action taken)
             1: Buy signal (will buy if cash available)
        
        Example:
            def decisionFunction(self, row):
                if row["momentum_rsi"] < 30:
                    return 1  # Oversold, buy
                elif row["momentum_rsi"] > 70:
                    return -1  # Overbought, sell
                return 0  # Hold
        """
        raise NotImplementedError("You need to overwrite the decisionFunction!!!!")
    
    def getLatestDecision(self, data: pd.DataFrame, nrMedianLatest: int = 1) -> int:
        """
        Get the latest trading decision by applying decisionFunction to data.
        
        Args:
            data: DataFrame with market data
            nrMedianLatest: Number of latest rows to average (default: 1)
            
        Returns:
            Averaged decision signal (-1, 0, or 1)
        """
        if not isinstance(data, pd.DataFrame):
            raise ValueError("Data must be a pandas DataFrame")
        if len(data) == 0:
            return 0  # No data, hold
        
        # Work on a copy to avoid mutating the original DataFrame
        data_copy = data.copy()
        data_copy["signal"] = data_copy.apply(self.decisionFunction, axis=1)
        
        # Ensure we don't try to access more rows than available
        nrMedianLatest = min(nrMedianLatest, len(data_copy))
        if nrMedianLatest <= 0:
            return 0
        
        # Get the last nrMedianLatest signals and return their mean
        latest_signals = data_copy["signal"].iloc[-nrMedianLatest:]
        return int(latest_signals.mean())
    
    def run(self) -> None:
        """
        Execute one iteration of the bot and log results.
        
        Catches exceptions and logs them to the database before re-raising.
        """
        # Refresh dbBot to ensure it's attached to a session
        self.dbBot = self._bot_repository.create_or_get_bot(self.bot_name)
        bot_name = self.bot_name
        decision = -2
        try:
            decision = self.makeOneIteration()
            # Refresh again after makeOneIteration in case portfolio was updated
            self.dbBot = self._bot_repository.create_or_get_bot(self.bot_name)
            cash = self.dbBot.portfolio.get("USD", 0)
            
            # Handle multi-asset bots gracefully
            if self.symbol:
                holding = self.dbBot.portfolio.get(self.symbol, 0)
                holding_info = f"Holding: {holding}"
            else:
                # For multi-asset bots, show portfolio summary
                non_usd_holdings = {k: v for k, v in self.dbBot.portfolio.items() if k != "USD" and v > 0}
                holding_info = f"Holdings: {len(non_usd_holdings)} assets"
            
            print(f"Decision: {decision}")
            with get_db_session() as session:
                run = RunLog(
                    bot_name=bot_name,
                    success=True,
                    result=f"Decision: {decision}, Cash: {cash}, {holding_info}, portfolio: {str(self.dbBot.portfolio)}",
                )
                session.add(run)
                # Context manager will commit automatically
        except Exception as e:
            print(f"Error in makeOneIteration: {e}")
            with get_db_session() as session:
                run = RunLog(
                    bot_name=bot_name,
                    success=False,
                    result=str(e),
                )
                session.add(run)
                # Context manager will commit automatically
            raise e
    
    def makeOneIteration(self) -> int:
        """
        Execute one iteration of the trading bot.
        
        Default implementation:
        1. Fetches data with technical indicators
        2. Gets decision by applying decisionFunction() to data
        3. Executes buy/sell based on decision
        
        **When to override:**
        - Multi-asset bots (must override if self.symbol is None)
        - External data sources (e.g., Fear & Greed Index API)
        - Portfolio optimization strategies
        - Custom data processing beyond row-by-row logic
        
        **When NOT to override:**
        - Simple single-asset strategies (just implement decisionFunction() instead)
        - Strategies that only need different timeframes (set interval/period in __init__)
        
        Returns:
            -1: Sold
             0: No action
             1: Bought
        
        Raises:
            NotImplementedError: If self.symbol is None (multi-asset bot) and method not overridden
        """
        # For multi-asset bots, this method must be overridden
        if self.symbol is None:
            raise NotImplementedError(
                "Multi-asset bots must override makeOneIteration(). "
                "Single-asset bots must provide a symbol in __init__()."
            )
        
        # Refresh dbBot to ensure it's attached to a session
        self.dbBot = self._bot_repository.create_or_get_bot(self.bot_name)
        data = self.getYFDataWithTA(saveToDB=True, interval=self.interval, period=self.period)
        decision = self.getLatestDecision(data)
        cash = self.dbBot.portfolio.get("USD", 0)
        holding = self.dbBot.portfolio.get(self.symbol, 0)
        if decision == 1 and cash > 0:
            self.buy(self.symbol)
            return 1
        elif decision == -1 and holding > 0:
            self.sell(self.symbol)
            return -1
        else:
            print("doing nothing!")
        return 0
    
    def local_optimize(
        self,
        param_grid: Optional[Dict[str, List[Any]]] = None,
        objective: str = "sharpe_ratio",
        initial_capital: float = 10000.0,
        n_jobs: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Local-only helper: run hyperparameter optimization for this bot's class.
        
        Uses either the provided param_grid or self.param_grid (if defined as class attribute).
        Prints the best combination in a format easy to copy-paste into __init__ defaults.
        
        Args:
            param_grid: Optional parameter grid to use. If None, uses self.param_grid or class attribute.
            objective: Metric to maximize ("sharpe_ratio" or "yearly_return")
            initial_capital: Starting capital for backtests
            n_jobs: Number of parallel jobs (None = auto-detect)
        
        Returns:
            Full optimization results dictionary
        
        Raises:
            ValueError: If no param_grid is defined
        """
        from .hyperparameter_tuning import tune_hyperparameters
        
        # Use provided grid, or fall back to class attribute
        grid = param_grid or getattr(self, "param_grid", None) or self.__class__.param_grid
        if not grid:
            raise ValueError(
                f"No param_grid defined for {self.__class__.__name__}. "
                f"Either define param_grid as a class attribute or pass it to local_optimize()."
            )
        
        print("=" * 60)
        print(f"Hyperparameter optimization for {self.__class__.__name__}")
        print("=" * 60)
        
        results = tune_hyperparameters(
            self.__class__,
            grid,
            objective=objective,
            initial_capital=initial_capital,
            verbose=True,
            n_jobs=n_jobs,
        )
        
        print("\n" + "=" * 60)
        print("Best parameters (paste into __init__ defaults):")
        print("=" * 60)
        for key, value in results["best_params"].items():
            print(f"    {key}: {value},")
        print()
        
        return results
    
    def local_backtest(self, initial_capital: float = 10000.0) -> Dict[str, Any]:
        """
        Local-only helper: run a backtest with current instance parameters.
        
        Args:
            initial_capital: Starting capital for backtest
        
        Returns:
            Backtest results dictionary
        """
        from .backtest import backtest_bot
        
        results = backtest_bot(self, initial_capital=initial_capital)
        print(f"\n--- Backtest Results: {self.bot_name} ---")
        print(f"Yearly Return: {results['yearly_return']:.2%}")
        print(f"Sharpe Ratio: {results['sharpe_ratio']:.2f}")
        print(f"Number of Trades: {results['nrtrades']}")
        print(f"Max Drawdown: {results['maxdrawdown']:.2%}")
        return results
    
    def local_development(
        self,
        param_grid: Optional[Dict[str, List[Any]]] = None,
        objective: str = "sharpe_ratio",
        initial_capital: float = 10000.0,
        n_jobs: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Convenience wrapper for the typical local development workflow:
        
        1) Optimize hyperparameters for this bot's class
        2) Backtest a bot instance constructed with the best parameters
        
        Does NOT modify __init__ defaults; you still paste them manually.
        
        Args:
            param_grid: Optional parameter grid to use. If None, uses self.param_grid or class attribute.
            objective: Metric to maximize ("sharpe_ratio" or "yearly_return")
            initial_capital: Starting capital for backtests
            n_jobs: Number of parallel jobs (None = auto-detect)
        
        Returns:
            Optimization results dictionary with 'best_params' and performance metrics
        
        Example:
            bot = MyBot()
            results = bot.local_development()
            # Prints best parameters in copy-paste format
            # Then backtests with those parameters
            # Copy the printed parameters into __init__ defaults
        """
        # Step 1: Optimize
        opt_results = self.local_optimize(
            param_grid=param_grid,
            objective=objective,
            initial_capital=initial_capital,
            n_jobs=n_jobs,
        )
        
        # Step 2: Backtest with best parameters
        print("\n" + "=" * 60)
        print("Backtesting with best parameters...")
        print("=" * 60)
        best_bot = self.__class__(**opt_results["best_params"])
        best_bot.local_backtest(initial_capital=initial_capital)
        
        return opt_results