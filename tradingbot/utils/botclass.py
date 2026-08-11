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

import logging
from collections.abc import Callable
from typing import Any

import pandas as pd

from .bot_repository import BotRepository
from .config import setup_logging
from .data_service import DataService
from .db import RunLog, get_db_session, init_db
from .portfolio_manager import PortfolioManager

logger = logging.getLogger(__name__)

# Below this many units a holding is float residue, not a position. Matches the
# zero-holding cutoff in PortfolioManager.sell.
DUST_QTY = 1e-6


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
    param_grid: dict[str, list[Any]] | None = None

    # If True, symbols held but absent from self.tickers are sold to cash on the
    # multi-ticker path. Default False because KronosTraderBot rebuilds its
    # universe from the predictions table on every run, so a single day's gap
    # would otherwise liquidate and re-enter the whole book.
    LIQUIDATE_UNTRACKED: bool = False

    def __init__(
        self,
        name: str,
        symbol: str | None = None,
        tickers: list[str] | None = None,
        interval: str = "1m",
        period: str = "1d",
        benchmark_tickers: list[str] | None = None,
        **kwargs,
    ):
        """
        Initialize a trading bot.

        Args:
            name: Unique name for the bot (used for database identification)
            symbol: Trading symbol (e.g., "EURUSD=X", "^XAU", "QQQ")
                    Optional for multi-asset bots that override makeOneIteration()
            tickers: List of trading symbols for multi-ticker strategies.
                     If provided, symbol is ignored and set to None.
                     e.g. tickers=["SPY", "QQQ", "GLD"]
            interval: Data interval (e.g., "1m", "5m", "1h", "1d") - default: "1m"
            period: Data period (e.g., "1d", "5d", "1mo", "1y") - default: "1d"
            benchmark_tickers: Subset of `tickers` that is loaded for data but
                     NEVER traded, and excluded from the equal-weight divisor.
                     For strategies that need a relative-strength baseline (e.g.
                     GoldenButterflyMomBot carries SPY for its RRG computation).
                     Without this, such a bot's capital is divided by a count
                     that includes an asset it can never hold, permanently
                     stranding that share in cash.
            **kwargs: Arbitrary hyperparameters that will be stored in self.params
                     and can be accessed by subclasses for flexible parameterization
        """
        setup_logging()
        self.bot_name = name  # Store name separately to avoid DetachedInstanceError

        # Resolve the universe BEFORE touching the database: a misconfigured bot
        # must fail without first materialising a $10k row in `bots`.
        if tickers is not None:
            # Guard: accept a bare string as a single-element list
            if isinstance(tickers, str):
                tickers = [tickers]
            # Dedupe: a repeated ticker would both inflate the divisor and get
            # traded twice.
            self.tickers: list[str] = list(dict.fromkeys(tickers))
            self.symbol: str | None = None
        elif symbol is not None:
            self.tickers = [symbol]
            self.symbol = symbol
        else:
            self.tickers = []
            self.symbol = None

        if isinstance(benchmark_tickers, str):
            benchmark_tickers = [benchmark_tickers]
        self.benchmark_tickers: list[str] = list(dict.fromkeys(benchmark_tickers or []))

        unknown = [t for t in self.benchmark_tickers if t not in self.tickers]
        if unknown:
            raise ValueError(
                f"{name}: benchmark_tickers {unknown} are not in tickers {self.tickers}. "
                "A benchmark must also be listed in tickers, or its data is never fetched."
            )
        if self.tickers and not self.tradeable_tickers:
            raise ValueError(f"{name}: every ticker is a benchmark — nothing left to trade.")

        init_db()  # Ensure database is initialized before first access
        self.dbBot = BotRepository.create_or_get_bot(name)
        self.interval = interval
        self.period = period

        # Store hyperparameters in a dictionary for flexible access
        self.params = kwargs.copy() if kwargs else {}

        # Initialize services
        self._data_service = DataService()
        self._bot_repository = BotRepository  # staticmethod namespace, never instantiated
        self._portfolio_manager = PortfolioManager(
            bot=self.dbBot,
            bot_name=self.bot_name,
            data_service=self._data_service,
            bot_repository=self._bot_repository,
        )

        # Maintain backward compatibility for data caching
        self.data: pd.DataFrame | None = None
        self.datas: dict[str, pd.DataFrame | None] = {}  # per-ticker cache for multi-ticker bots
        self.datasettings: tuple[str | None, str | None] = (None, None)

    @property
    def tradeable_tickers(self) -> list[str]:
        """
        Tickers this bot may actually hold: self.tickers minus benchmark_tickers.

        Benchmarks are fetched into self.datas (a strategy may need one as a
        relative-strength baseline) but are excluded from the equal-weight
        divisor and are never bought or sold. Defaults to self.tickers, so bots
        that declare no benchmarks are unaffected.

        Written defensively because tests stub out Bot.__init__ entirely and
        backtest_bot() accepts arbitrary instances. It is also called from
        __init__ during validation, before the attributes are guaranteed set.
        """
        benchmarks = set(getattr(self, "benchmark_tickers", ()) or ())
        return [t for t in getattr(self, "tickers", ()) if t not in benchmarks]

    @property
    def backtest_type(self) -> str:
        """
        Classify the bot's backtesting mode.

        Note this counts self.tickers, NOT tradeable_tickers: a bot with one
        tradeable ticker plus a benchmark must still take the multi-ticker path,
        because the single-asset path fetches only self.symbol (None for a
        tickers= bot) and would never load the benchmark's data.

        Returns:
            "single_asset"  — decisionFunction overridden + single ticker  → backtestable
            "multi_asset"   — decisionFunction overridden + multiple tickers → backtestable
            "event_driven"  — only makeOneIteration overridden              → NOT backtestable
            "unknown"       — neither method overridden                      → NOT backtestable
        """
        has_df = type(self).decisionFunction is not Bot.decisionFunction
        has_moi = type(self).makeOneIteration is not Bot.makeOneIteration
        if has_df and len(self.tickers) > 1:
            return "multi_asset"
        if has_df and len(self.tickers) == 1:
            return "single_asset"
        if has_moi:
            return "event_driven"
        return "unknown"

    @property
    def can_backtest(self) -> bool:
        """
        True if this bot can be backtested with local_backtest() / local_optimize().

        Only data-driven bots that implement decisionFunction() are backtestable.
        Event-driven bots (makeOneIteration only) must use run() for live execution.
        """
        return self.backtest_type in ("single_asset", "multi_asset")

    def _assert_backtestable(self) -> None:
        """Raise a clear error if this bot is not backtestable."""
        if not self.can_backtest:
            raise NotImplementedError(
                f"{self.__class__.__name__} is not backtestable "
                f"(backtest_type='{self.backtest_type}'). "
                "Only data-driven bots that implement decisionFunction() support "
                "local_backtest() / local_optimize(). "
                "Use bot.run() for live execution."
            )

    # Data fetching methods - delegate to DataService
    def _parsePeriodToDateRange(self, period: str) -> tuple[pd.Timestamp, pd.Timestamp]:
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
        start_date: pd.Timestamp | None = None,
        end_date: pd.Timestamp | None = None,
        interval: str | None = None,
    ) -> pd.DataFrame:
        """
        Load data from database for a symbol.

        Args:
            symbol: Trading symbol to query
            start_date: Optional start date (timezone-aware UTC)
            end_date: Optional end date (timezone-aware UTC)
            interval: Bar size to read; defaults to this bot's own interval.
                      Rows stored at other bar sizes are never returned.

        Returns:
            DataFrame with columns: symbol, timestamp, open, high, low, close, volume
            Empty DataFrame if no data found
        """
        # Keyword args, not positional: get_data_from_db takes `interval` second,
        # so a positional call would silently pass start_date as the interval.
        return self._data_service.get_data_from_db(
            symbol=symbol,
            interval=interval or self.interval,
            start_date=start_date,
            end_date=end_date,
        )

    def getYFData(
        self,
        symbol: str | None = None,
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
        is_primary = False
        if not symbol:
            if self.symbol is None:
                raise ValueError("symbol parameter is required when self.symbol is None (multi-asset bot)")
            symbol = self.symbol
            is_primary = True
        elif symbol == self.symbol:
            is_primary = True

        data = self._data_service.get_yf_data(
            symbol=symbol,
            interval=interval,
            period=period,
            save_to_db=saveToDB,
            use_cache=True,
        )

        # Update cache for backward compatibility: only if it's the primary symbol
        # or it's a single-ticker bot.
        if (is_primary or len(self.tickers) <= 1) and (interval, period) == self.datasettings:
            self.data = data

        return data

    def getYFDataWithTA(
        self,
        symbol: str | None = None,
        interval: str = "1m",
        period: str = "1d",
        saveToDB: bool = False,
        features: list[str] | None = None,
    ) -> pd.DataFrame:
        """
        Fetch market data with technical analysis indicators.

        Data fetching strategy:
        - Checks database first for existing data
        - Only fetches from yfinance if data is missing or stale
        - If saveToDB=True, saves fetched data to database for future reuse

        Note: For repeated backtests or hyperparameter tuning, set saveToDB=True
        to enable efficient data reuse.

        Args:
            symbol: Trading symbol (defaults to self.symbol)
            interval: Data interval (e.g., "1m", "5m", "1h", "1d")
            period: Data period (e.g., "1d", "5d", "1mo", "1y")
            saveToDB: Whether to save fetched data to database. Set to True for
                     historical backtests to enable data reuse.
            features: Optional list of specific TA indicator column names to keep.
                      If provided, drops other TA columns to save memory.

        Returns:
            DataFrame with market data and technical analysis features
        """
        is_primary = False
        if not symbol:
            if self.symbol is None:
                raise ValueError("symbol parameter is required when self.symbol is None (multi-asset bot)")
            symbol = self.symbol
            is_primary = True
        elif symbol == self.symbol:
            is_primary = True

        data = self._data_service.get_yf_data_with_ta(
            symbol=symbol,
            interval=interval,
            period=period,
            save_to_db=saveToDB,
            features=features,
        )

        # Update cache for backward compatibility: only if it's the primary symbol
        # or it's a single-ticker bot.
        if (is_primary or len(self.tickers) <= 1) and (interval, period) == self.datasettings:
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

    def addPdDFToDb(self, df: pd.DataFrame, interval: str | None = None) -> None:
        """
        Add DataFrame rows to database, skipping duplicates.

        Only inserts rows newer than the latest stored for this (symbol, interval).

        Args:
            df: DataFrame with columns: symbol, timestamp, open, high, low, close, volume
            interval: Bar size these rows were fetched at; defaults to this bot's
                      own interval. Filing rows under the wrong bar size corrupts
                      every later read (see HistoricData's docstring).
        """
        self._data_service.add_pd_df_to_db(df, interval=interval or self.interval)

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
        # Pass the per-ticker cache if available, else fallback to self.data
        cached = self.datas.get(symbol, self.data)
        return self._data_service.get_latest_price(symbol, cached_data=cached)

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
    def buy(self, symbol: str, quantity_usd: float = -1) -> None:
        """
        Buy a quantity of the specified symbol.

        Args:
            symbol: Trading symbol to buy
            quantity_usd: Amount in USD to spend (-1 means use all available cash)
        """
        # Use per-ticker cache if available
        cached = self.datas.get(symbol, self.data)
        self._portfolio_manager.buy(symbol, quantity_usd=quantity_usd, cached_data=cached)
        # Refresh dbBot reference after portfolio update
        self.dbBot = self._bot_repository.create_or_get_bot(self.bot_name)

    def sell(self, symbol: str, quantity_usd: float = -1) -> None:
        """
        Sell a quantity of the specified symbol.

        Args:
            symbol: Trading symbol to sell
            quantity_usd: Amount in USD to sell (-1 means sell all holdings)
        """
        # Use per-ticker cache if available
        cached = self.datas.get(symbol, self.data)
        self._portfolio_manager.sell(symbol, quantity_usd=quantity_usd, cached_data=cached)
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

            logger.info("Decision: %s", decision)
            with get_db_session() as session:
                run = RunLog(
                    bot_name=bot_name,
                    success=True,
                    result=f"Decision: {decision}, Cash: {cash}, {holding_info}",
                )
                session.add(run)
                # Context manager will commit automatically
        except Exception as e:
            logger.error("Error in makeOneIteration: %s", e)
            with get_db_session() as session:
                run = RunLog(
                    bot_name=bot_name,
                    success=False,
                    result=str(e),
                )
                session.add(run)
                # Context manager will commit automatically
            raise e

    def get_ai_tools(self) -> list[Any]:
        """
        Return custom LangChain tools for this bot. Override in subclasses to add
        bot-specific tools (e.g. get_tradeable_symbols, run_optimization).
        These are merged with the base tools when calling run_ai().
        """
        return []

    def run_ai(
        self,
        system_prompt: str,
        user_message: str,
        model: str | None = None,
        max_tool_rounds: int = 5,
        extra_tools: list[Any] | None = None,
        tool_names: list[str] | None = None,
    ) -> str:
        """
        Run the AI with tools (market data, portfolio, recent trades, plus any custom tools) bound to this bot.
        Uses the main LLM (OPENROUTER_MAIN_MODEL, default deepseek/deepseek-v3.2).
        Pass model= to override. Merge custom tools via get_ai_tools() or extra_tools=.
        Optional tool_names= whitelists which base tools to include. Requires OPENROUTER_API_KEY.
        """
        from .aitools import run_ai_with_tools

        merged_extra = list(self.get_ai_tools()) + (extra_tools or [])
        return run_ai_with_tools(
            system_prompt,
            user_message,
            self,
            model=model,
            max_tool_rounds=max_tool_rounds,
            extra_tools=merged_extra if merged_extra else None,
            tool_names=tool_names,
        )

    def run_ai_simple(
        self,
        system_prompt: str,
        user_message: str,
        model: str | None = None,
    ) -> str:
        """
        Run the AI for a single-turn, no-tools task (summarization, extraction,
        classification, rewriting). Uses the cheap LLM (OPENROUTER_CHEAP_MODEL,
        default openrouter/free). Use run_ai() when you need tool access.
        """
        from .aitools import run_ai_simple as _run_ai_simple

        return _run_ai_simple(system_prompt, user_message, model=model)

    def run_ai_simple_with_fallback(
        self,
        system_prompt: str,
        user_message: str,
        sanity_check: Callable[[str], bool] | None = None,
        fallback_to_main: bool = True,
    ) -> str:
        """
        Run a simple (no-tools) task with cheap LLM first; verify output for sanity;
        if validation fails, retry with main LLM. Prefer this over run_ai_simple when
        you want to save cost but still guarantee sane results.

        sanity_check: Optional callable(response) -> bool. If None, uses a default
            check (non-empty, no refusal/error prefix).
        fallback_to_main: If True and sanity check fails, retry with main model.
        """
        from .aitools import run_ai_simple_with_fallback as _run_with_fallback

        return _run_with_fallback(
            system_prompt,
            user_message,
            sanity_check=sanity_check,
            fallback_to_main=fallback_to_main,
        )

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
        # Truly uninitialized bot (no symbol and no tickers)
        if self.symbol is None and len(self.tickers) == 0:
            raise NotImplementedError(
                "Bot has no symbol or tickers configured. "
                "Pass symbol= for single-asset or tickers= for multi-asset bots."
            )

        # Multi-ticker path: delegate to _run_multi_ticker_iteration
        if len(self.tickers) > 1:
            return self._run_multi_ticker_iteration()

        # Single-asset path
        # Refresh dbBot to ensure it's attached to a session
        self.dbBot = self._bot_repository.create_or_get_bot(self.bot_name)
        data = self.getYFDataWithTA(saveToDB=True, interval=self.interval, period=self.period)
        # Make full dataset available so decisionFunction can access history
        # (e.g. Hurst exponent, rolling z-scores) without overriding makeOneIteration.
        self.data = data
        self.datasettings = (self.interval, self.period)
        decision = self.getLatestDecision(data)
        cash = self.dbBot.portfolio.get("USD", 0)
        holding = self.dbBot.portfolio.get(self.symbol, 0)
        if decision == 1 and cash > 0 and self.symbol is not None:
            self.buy(self.symbol)
            return 1
        elif decision == -1 and holding > 0 and self.symbol is not None:
            self.sell(self.symbol)
            return -1
        else:
            logger.info("No trade action taken (hold).")
        return 0

    def _multi_ticker_target_weights(
        self,
        decisions: dict[str, int],
        prices: dict[str, float],
        portfolio: dict[str, float],
    ) -> dict[str, float]:
        """
        Turn per-ticker signals into target portfolio weights.

        Pure: no I/O, no DB, no mutation of self. All of the allocation policy
        lives here so it can be unit-tested exhaustively — which matters because
        the caller reconciles the WHOLE BOOK in one transaction, so a mistake
        here liquidates a position rather than merely mis-sizing it.

        Rules, each a deliberate choice:
          * The divisor is len(tradeable_tickers) — benchmarks are excluded.
          * signal ==  1  -> one full equal-weight sleeve (buy up OR trim down).
          * signal == -1  -> zero. A full exit, so the no-trade band cannot
                             strand a small position that was told to leave.
          * signal ==  0  -> HOLD: never funded, but capped at one sleeve.
                             "Don't add" is a statement about initiating
                             exposure; the cap is a risk limit that applies
                             whatever the signal. Funding a 0 leg would be a
                             semantic inversion for KronosTraderBot, which
                             returns 0 for "no prediction available" and would
                             then buy every symbol it has no opinion about.
          * A held benchmark is sold — declaring it non-tradeable declares it is
            not part of the book, and nothing else could ever exit it.
          * A held symbol outside self.tickers keeps its exact current weight and
            is excluded from the sizing base, unless LIQUIDATE_UNTRACKED. Default
            off because KronosTraderBot rebuilds its universe from the
            predictions table every run: a one-day gap would otherwise liquidate
            and re-enter the entire book.
          * An unpriceable ticker leaves the divisor rather than being sized
            against an assumed value of zero (which would buy a full sleeve on
            top of a position already held).

        Returns weights summing to 1.0 (USD absorbs the residual), or {} if the
        portfolio cannot be sized this run.
        """
        benchmarks = set(self.benchmark_tickers)
        held = {s: q for s, q in portfolio.items() if s != "USD" and q > DUST_QTY}
        cash = float(portfolio.get("USD", 0.0))

        def _px(sym: str) -> float:
            p = prices.get(sym) or 0.0
            return float(p) if p > 0 else 0.0

        # Unpriceable holdings contribute 0 — which is exactly how
        # rebalance_portfolio values them, so our weights and its valuation agree
        # and it computes diff == 0 and leaves them alone.
        unpriceable = [s for s in held if _px(s) <= 0]
        if unpriceable:
            logger.error(
                "%s: no price for held symbols %s — valuing them at 0 and not trading them",
                self.bot_name,
                sorted(unpriceable),
            )

        values = {s: q * _px(s) for s, q in held.items()}
        total_value = cash + sum(values.values())
        if total_value <= 0:
            logger.warning("%s: portfolio values at $0 — nothing to rebalance", self.bot_name)
            return {}

        tradeable = [t for t in self.tradeable_tickers if _px(t) > 0]
        dropped = [t for t in self.tradeable_tickers if _px(t) <= 0]
        if dropped:
            logger.error("%s: dropping unpriceable tickers from this run: %s", self.bot_name, dropped)
        if not tradeable:
            logger.error("%s: no priceable tradeable tickers — skipping rebalance", self.bot_name)
            return {}

        tradeable_set = set(tradeable)
        keep: dict[str, float] = {}
        if not self.LIQUIDATE_UNTRACKED:
            keep = {s: v for s, v in values.items() if s not in tradeable_set and s not in benchmarks and v > 0}
        if keep:
            logger.warning(
                "%s: holding $%.2f in symbols outside its universe (%s); excluded from the "
                "sizing base and left untouched. Set LIQUIDATE_UNTRACKED to sell them.",
                self.bot_name,
                sum(keep.values()),
                sorted(keep),
            )
        stranded = {s: v for s, v in values.items() if s in benchmarks and v > 0}
        if stranded:
            logger.warning(
                "%s: liquidating benchmark holdings %s ($%.2f) — benchmarks are not tradeable",
                self.bot_name,
                sorted(stranded),
                sum(stranded.values()),
            )

        investable = total_value - sum(keep.values())
        if investable <= 0:
            logger.warning("%s: nothing investable after untracked holdings", self.bot_name)
            return {}

        target_per_leg = investable / len(tradeable)

        target_values: dict[str, float] = dict(keep)
        for ticker in tradeable:
            signal = decisions.get(ticker, 0)
            current = values.get(ticker, 0.0)
            if signal == -1:
                continue  # target zero: omit entirely, rebalance sells it out
            wanted = target_per_leg if signal == 1 else min(current, target_per_leg)
            if wanted > 0:
                target_values[ticker] = wanted

        weights = {s: v / total_value for s, v in target_values.items() if v > 0}
        non_usd = sum(weights.values())
        if non_usd > 1.0:  # float defence only; the arithmetic above cannot exceed 1
            logger.error("%s: target weights summed to %.6f — scaling down", self.bot_name, non_usd)
            weights = {s: w / non_usd for s, w in weights.items()}
            non_usd = 1.0
        weights["USD"] = max(0.0, 1.0 - non_usd)
        return weights

    def _run_multi_ticker_iteration(self) -> int:
        """
        Execute one live-trading iteration for a multi-ticker bot.

        Loads data for every ticker (benchmarks included, so cross-ticker
        strategies see them in self.datas), asks for a decision on the tradeable
        ones only, converts those decisions into target weights, and reconciles
        the whole book in ONE locked transaction via rebalancePortfolio().

        The previous implementation issued N separate buy/sell transactions in
        ticker order. Because PortfolioManager.buy silently clamps to available
        cash and never retries, sale proceeds arrived AFTER every buy had already
        been sized against pre-sale cash — permanent, silent under-investment
        whenever a sell sorted after a buy. rebalance_portfolio executes all
        sells before any buy inside one transaction, which fixes that
        structurally, and gives correct partial trimming for free.

        Returns:
            Number of legs whose target differs materially from their current value
        """
        self.dbBot = self._bot_repository.create_or_get_bot(self.bot_name)
        tradeable = self.tradeable_tickers

        # Phase 1: load ALL tickers so self.datas is complete before any
        # decisionFunction call (GoldenButterflyMomBot reads self.datas[SPY]).
        for ticker in self.tickers:
            self.datas[ticker] = self.getYFDataWithTA(
                symbol=ticker,
                saveToDB=True,
                interval=self.interval,
                period=self.period,
            )

        # Phase 2: decide. A benchmark is never asked for a decision at all.
        decisions: dict[str, int] = {}
        for ticker in tradeable:
            self._current_ticker = ticker
            decisions[ticker] = self.getLatestDecision(self.datas[ticker])
        logger.info("%s decisions: %s", self.bot_name, decisions)

        # Phase 3: one batch price read — the same call rebalance_portfolio uses
        # to value the book, so our weights and its valuation cannot disagree.
        portfolio = dict(self.dbBot.portfolio or {})
        symbols = sorted({*self.tickers, *(s for s in portfolio if s != "USD")})
        prices = self.getLatestPricesBatch(symbols)

        weights = self._multi_ticker_target_weights(decisions, prices, portfolio)
        if not weights:
            return 0

        # Warm the module-level TTL price cache from data we already hold, so the
        # per-leg get_latest_price calls inside the rebalance are cache hits
        # rather than yfinance I/O executed while holding the bots row lock.
        for ticker in tradeable:
            try:
                self.getLatestPrice(ticker)
            except Exception as exc:
                logger.warning("Price prewarm failed for %s: %s", ticker, exc)

        total_value = portfolio.get("USD", 0.0) + sum(portfolio.get(s, 0.0) * (prices.get(s) or 0.0) for s in symbols)
        moves = sum(
            1
            for sym, w in weights.items()
            if sym != "USD" and abs(w * total_value - portfolio.get(sym, 0.0) * (prices.get(sym) or 0.0)) > 1.0
        )
        logger.info(
            "%s target weights: %s",
            self.bot_name,
            {s: round(w, 4) for s, w in sorted(weights.items())},
        )
        self.rebalancePortfolio(weights)
        return moves

    def local_optimize(
        self,
        param_grid: dict[str, list[Any]] | None = None,
        objective: str = "sharpe_ratio",
        initial_capital: float = 10000.0,
        n_jobs: int | None = None,
        param_sample_ratio: float = 1.0,
    ) -> dict[str, Any]:
        """
        Local-only helper: run hyperparameter optimization for this bot's class.

        Uses either the provided param_grid or self.param_grid (if defined as class attribute).
        Prints the best combination in a format easy to copy-paste into __init__ defaults.

        Args:
            param_grid: Optional parameter grid to use. If None, uses self.param_grid or class attribute.
            objective: Metric to maximize ("sharpe_ratio" or "yearly_return")
            initial_capital: Starting capital for backtests
            n_jobs: Number of parallel jobs (None = auto-detect)
            param_sample_ratio: Fraction of param combinations to test (0.0–1.0). 1.0 = all (default).

        Returns:
            Full optimization results dictionary

        Raises:
            ValueError: If no param_grid is defined
        """
        from .hyperparameter_tuning import tune_hyperparameters

        self._assert_backtestable()

        # Use provided grid, or fall back to class attribute
        grid = param_grid or getattr(self, "param_grid", None) or self.__class__.param_grid
        if not grid:
            raise ValueError(
                f"No param_grid defined for {self.__class__.__name__}. "
                f"Either define param_grid as a class attribute or pass it to local_optimize()."
            )

        logger.info("=" * 60)
        logger.info(f"Hyperparameter optimization for {self.__class__.__name__}")
        logger.info("=" * 60)

        results = tune_hyperparameters(
            self.__class__,
            grid,
            objective=objective,
            initial_capital=initial_capital,
            verbose=True,
            n_jobs=n_jobs,
            param_sample_ratio=param_sample_ratio,
        )

        logger.info("\n" + "=" * 60)
        logger.info("Best parameters (paste into __init__ defaults):")
        logger.info("=" * 60)
        for key, value in results["best_params"].items():
            logger.info(f"    {key}: {value},")

        return results

    def local_backtest(self, initial_capital: float = 10000.0) -> dict[str, Any]:
        """
        Local-only helper: run a backtest with current instance parameters.

        Args:
            initial_capital: Starting capital for backtest

        Returns:
            Backtest results dictionary
        """
        from .backtest import backtest_bot

        self._assert_backtestable()
        results = backtest_bot(self, initial_capital=initial_capital)
        logger.info(f"\n--- Backtest Results: {self.bot_name} ---")
        logger.info(f"Yearly Return: {results['yearly_return']:.2%}")
        logger.info(f"Buy & Hold Return: {results['buy_hold_return']:.2%}")
        logger.info(f"Outperformance vs B&H: {(results['yearly_return'] - results['buy_hold_return']):+.2%}")
        logger.info(f"Sharpe Ratio: {results['sharpe_ratio']:.2f}")
        logger.info(f"Number of Trades: {results['nrtrades']}")
        logger.info(f"Max Drawdown: {results['maxdrawdown']:.2%}")
        return results

    def local_development(
        self,
        param_grid: dict[str, list[Any]] | None = None,
        objective: str = "sharpe_ratio",
        initial_capital: float = 10000.0,
        n_jobs: int | None = None,
        param_sample_ratio: float = 1.0,
    ) -> dict[str, Any]:
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
            param_sample_ratio: Fraction of param combinations to test (0.0–1.0). 1.0 = all (default).
                               e.g. 0.2 = randomly test 20% of the grid.

        Returns:
            Optimization results dictionary with 'best_params' and performance metrics

        Example:
            bot = MyBot()
            results = bot.local_development()
            # Prints best parameters in copy-paste format
            # Then backtests with those parameters
            # Copy the printed parameters into __init__ defaults
        """
        self._assert_backtestable()

        # Step 1: Optimize
        opt_results = self.local_optimize(
            param_grid=param_grid,
            objective=objective,
            initial_capital=initial_capital,
            n_jobs=n_jobs,
            param_sample_ratio=param_sample_ratio,
        )

        # Step 2: Backtest with best parameters
        logger.info("\n" + "=" * 60)
        logger.info("Backtesting with best parameters...")
        logger.info("=" * 60)
        best_params = opt_results["best_params"]
        for key, value in best_params.items():
            logger.info(f"  {key}: {value}")
        best_bot = self.__class__(**best_params)
        best_bot.local_backtest(initial_capital=initial_capital)

        return opt_results
