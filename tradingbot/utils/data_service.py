"""Data service for fetching and managing market data."""

from typing import Optional, Tuple

import pandas as pd
import yfinance as yf
from cachetools import TTLCache
from ta import add_all_ta_features

from .constants import (
    FRESHNESS_TOLERANCE_MINUTES,
    PRICE_CACHE_MAXSIZE,
    PRICE_CACHE_TTL,
    REQUIRED_DATA_COLUMNS,
)
from .db import HistoricData, get_db_session
from .helpers import (
    ensure_utc_series,
    ensure_utc_timestamp,
    parse_period_to_date_range,
    validate_dataframe_columns,
)

# TTL cache for getLatestPrice
_price_cache = TTLCache(maxsize=PRICE_CACHE_MAXSIZE, ttl=PRICE_CACHE_TTL)


class DataService:
    """
    Service for fetching and managing market data from Yahoo Finance and database.
    
    Caching Behavior:
    - Instance-level cache: `self.data` and `self.datasettings` cache the last fetched
      (interval, period) combination per DataService instance. This is useful for
      repeated calls within the same instance but does not persist across instances.
    - Database persistence: For cross-run data reuse (e.g., in hyperparameter tuning
      or multiple backtests), set `save_to_db=True` when fetching data. Subsequent
      calls (even from new DataService instances) will check the database first and
      only fetch from yfinance if data is missing or stale (older than
      FRESHNESS_TOLERANCE_MINUTES).
    - Best practice: Use `save_to_db=True` for historical backtests and tuning to
      enable efficient data reuse across multiple runs.
    """
    
    def __init__(self):
        """Initialize the data service."""
        self.data: Optional[pd.DataFrame] = None
        self.datasettings: Tuple[Optional[str], Optional[str]] = (None, None)
    
    def get_data_from_db(
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
        with get_db_session() as session:
            query = session.query(HistoricData).filter_by(symbol=symbol)
            
            if start_date:
                start_date = ensure_utc_timestamp(start_date)
                query = query.filter(HistoricData.timestamp >= start_date)
            
            if end_date:
                end_date = ensure_utc_timestamp(end_date)
                query = query.filter(HistoricData.timestamp <= end_date)
            
            query = query.order_by(HistoricData.timestamp)
            results = query.all()
            
            if not results:
                return pd.DataFrame()
            
            # Convert to DataFrame
            data = pd.DataFrame([{
                "symbol": r.symbol,
                "timestamp": r.timestamp,
                "open": r.open,
                "high": r.high,
                "low": r.low,
                "close": r.close,
                "volume": r.volume,
            } for r in results])
            
            # Ensure timestamp is timezone-aware (UTC)
            if "timestamp" in data.columns:
                data["timestamp"] = pd.to_datetime(data["timestamp"])
                data["timestamp"] = ensure_utc_series(data["timestamp"])
            
            return data
    
    def get_yf_data(
        self,
        symbol: str,
        interval: str = "1m",
        period: str = "1d",
        save_to_db: bool = False,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """
        Fetch market data from Yahoo Finance, checking database first.
        
        Data fetching strategy:
        1. If use_cache=True and instance cache matches (interval, period), return cached data
        2. Otherwise, check database for existing data in the requested date range
        3. If DB data exists and is fresh (within FRESHNESS_TOLERANCE_MINUTES), use it
        4. If DB data is missing or stale, fetch from yfinance
        5. If save_to_db=True, save fetched data to database for future reuse
        
        Note: For repeated backtests or hyperparameter tuning, set save_to_db=True
        on the first fetch to populate the database. Subsequent fetches (even from
        new DataService instances) will reuse DB data and avoid yfinance calls.
        
        Args:
            symbol: Trading symbol
            interval: Data interval (e.g., "1m", "5m", "1h", "1d")
            period: Data period (e.g., "1d", "5d", "1mo", "1y")
            save_to_db: Whether to save fetched data to database. Set to True for
                       historical backtests to enable data reuse across runs.
            use_cache: Whether to use instance-level cached data if available.
                      Cache is per-instance and does not persist across instances.
            
        Returns:
            DataFrame with columns: symbol, timestamp, open, high, low, close, volume
        """
        # Check cache first
        if use_cache and (interval, period) == self.datasettings and self.data is not None:
            # Filter by symbol if data contains multiple symbols
            if symbol in self.data["symbol"].values:
                return self.data[self.data["symbol"] == symbol].copy()
            return self.data
        
        assert symbol, "Symbol must be provided"
        
        # Calculate date range from period
        start_date, end_date = parse_period_to_date_range(period)
        
        # Try to load from DB first
        db_data = self.get_data_from_db(symbol=symbol, start_date=start_date, end_date=end_date)
        
        # Check if DB data is fresh enough
        need_yf_fetch = False
        if db_data.empty:
            need_yf_fetch = True
        else:
            # Check freshness of latest DB data
            latest_db_timestamp = db_data["timestamp"].max()
            now = pd.Timestamp.now(tz="UTC")
            time_diff_minutes = (now - latest_db_timestamp).total_seconds() / 60
            
            if time_diff_minutes > FRESHNESS_TOLERANCE_MINUTES:
                need_yf_fetch = True
        
        # Fetch from yfinance if needed
        if need_yf_fetch:
            yf_data = yf.download(symbol, interval=interval, period=period)
            assert (
                len(yf_data) > 0
            ), f"No data found for {symbol} with interval {interval} and period {period}"
            yf_data = yf_data.swaplevel(axis=1)
            yf_data = yf_data[symbol]
            yf_data = yf_data.reset_index()
            yf_data["symbol"] = symbol
            yf_data.columns = [col.lower() for col in yf_data.columns]
            # Handle both "date" and "datetime" column names
            if "date" in yf_data.columns and "datetime" not in yf_data.columns:
                yf_data.rename(columns={"date": "datetime"}, inplace=True)
            # Change order
            yf_data = yf_data[
                [
                    "symbol",
                    "datetime",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                ]
            ]
            # Rename datetime to timestamp
            yf_data.rename(columns={"datetime": "timestamp"}, inplace=True)
            yf_data["timestamp"] = pd.to_datetime(yf_data["timestamp"])
            # Make timezone-aware (UTC) if not already
            yf_data["timestamp"] = ensure_utc_series(yf_data["timestamp"])
            
            # Merge DB data with yfinance data, removing duplicates
            if not db_data.empty:
                # Combine and remove duplicates based on timestamp
                data = pd.concat([db_data, yf_data], ignore_index=True)
                data = data.drop_duplicates(subset=["timestamp"], keep="last")
                data = data.sort_values("timestamp").reset_index(drop=True)
            else:
                data = yf_data
            
            # Save new data to DB if requested
            if save_to_db:
                self.add_pd_df_to_db(data)
        else:
            # Use DB data
            data = db_data
        
        if use_cache:
            self.datasettings = (interval, period)
            self.data = data
        
        return data
    
    def get_yf_data_with_ta(
        self,
        symbol: str,
        interval: str = "1m",
        period: str = "1d",
        save_to_db: bool = False,
    ) -> pd.DataFrame:
        """
        Fetch market data with technical analysis indicators.
        
        Args:
            symbol: Trading symbol
            interval: Data interval (e.g., "1m", "5m", "1h", "1d")
            period: Data period (e.g., "1d", "5d", "1mo", "1y")
            save_to_db: Whether to save fetched data to database
            
        Returns:
            DataFrame with market data and technical analysis features
        """
        data = self.get_yf_data(symbol, interval, period, save_to_db)
        data = add_all_ta_features(
            data, open="open", high="high", low="low", close="close", volume="volume"
        )
        data = data.ffill().bfill().fillna(0)
        return data
    
    def get_yf_data_multiple(
        self,
        symbols: list[str],
        interval: str = "1d",
        period: str = "3mo",
        save_to_db: bool = True,
    ) -> pd.DataFrame:
        """
        Fetch market data for multiple symbols efficiently, checking database first.
        
        Args:
            symbols: List of trading symbols to fetch
            interval: Data interval (e.g., "1m", "5m", "1h", "1d")
            period: Data period (e.g., "1d", "5d", "1mo", "3mo", "1y")
            save_to_db: Whether to save fetched data to database for each symbol
            
        Returns:
            DataFrame with columns: symbol, timestamp, open, high, low, close, volume
            Combined data from all symbols in long format
        """
        if not symbols:
            raise ValueError("Symbols list cannot be empty")
        
        # Calculate date range from period
        start_date, end_date = parse_period_to_date_range(period)
        
        # Check DB first for each symbol
        db_data_list = []
        symbols_to_fetch = []
        
        print(f"Checking database for {len(symbols)} symbols (interval={interval}, period={period})...")
        
        for symbol in symbols:
            # For daily data, don't filter by end_date (which is "now") because data timestamps
            # are at market close, not current time. Just get all data from start_date onwards.
            if interval == "1d":
                db_data = self.get_data_from_db(symbol=symbol, start_date=start_date, end_date=None)
            else:
                db_data = self.get_data_from_db(symbol=symbol, start_date=start_date, end_date=end_date)
            
            if db_data.empty:
                print(f"  {symbol}: No DB data found, fetching from yfinance")
                symbols_to_fetch.append(symbol)
            else:
                # Check freshness - for daily data, check if we have today's or yesterday's data
                # For intraday data, use time-based freshness check
                latest_db_timestamp = db_data["timestamp"].max()
                now = pd.Timestamp.now(tz="UTC")
                
                if interval == "1d":
                    # For daily data, check if we have data for today or yesterday
                    # Daily data timestamps are at market close (4 PM ET), so we compare dates
                    latest_db_date = latest_db_timestamp.date()
                    today_date = now.date()
                    yesterday_date = today_date - pd.Timedelta(days=1)
                    
                    # If we have today's or yesterday's data, consider it fresh enough
                    # (yesterday's data is fine if markets haven't closed today yet)
                    if latest_db_date >= yesterday_date:
                        db_data_list.append(db_data)
                        # Don't print for every symbol to avoid spam, but could add verbose mode
                    else:
                        # Data is older than yesterday, fetch fresh data
                        print(f"  {symbol}: DB data too old (latest: {latest_db_date}), fetching from yfinance")
                        symbols_to_fetch.append(symbol)
                else:
                    # For intraday data, use time-based freshness check
                    time_diff_minutes = (now - latest_db_timestamp).total_seconds() / 60
                    if time_diff_minutes > FRESHNESS_TOLERANCE_MINUTES:
                        print(f"  {symbol}: DB data stale ({time_diff_minutes:.1f} min old), fetching from yfinance")
                        symbols_to_fetch.append(symbol)
                    else:
                        db_data_list.append(db_data)
        
        print(f"Using DB data for {len(db_data_list)} symbols, fetching {len(symbols_to_fetch)} symbols from yfinance")
        
        # Download missing or stale symbols from yfinance
        yf_data_list = []
        if symbols_to_fetch:
            data = yf.download(symbols_to_fetch, interval=interval, period=period)
        
        if symbols_to_fetch and len(data) == 0:
            raise ValueError(
                f"No data found for symbols {symbols_to_fetch} with interval {interval} and period {period}"
            )
        
        # Process yfinance data if we fetched any
        if symbols_to_fetch:
            # Handle MultiIndex structure when multiple symbols are provided
            # yfinance returns: Level 0 = Attributes, Level 1 = Symbols (for multiple symbols)
            # For single symbol, it might not have MultiIndex
            if isinstance(data.columns, pd.MultiIndex):
                # After swaplevel: Level 0 = Symbols, Level 1 = Attributes
                data = data.swaplevel(axis=1)
            else:
                # Single symbol case - wrap in MultiIndex-like structure for consistent processing
                # Create a temporary structure to handle uniformly
                symbol = symbols_to_fetch[0] if len(symbols_to_fetch) == 1 else None
                if symbol:
                    # Add symbol level to columns
                    data.columns = pd.MultiIndex.from_product([[symbol], data.columns])
                    data = data.swaplevel(axis=1)
            
            for symbol in symbols_to_fetch:
                try:
                    # Check if symbol exists in the first level of MultiIndex columns
                    level_0_values = data.columns.get_level_values(0).unique()
                    if symbol in level_0_values:
                        sym_df = data[symbol].copy()
                        # Reset index to get Date/Datetime as column
                        sym_df = sym_df.reset_index()
                        # Add symbol column
                        sym_df["symbol"] = symbol
                        
                        # Rename columns to lowercase
                        sym_df.columns = [col.lower() for col in sym_df.columns]
                        
                        # Handle timestamp column
                        if "date" in sym_df.columns:
                            sym_df.rename(columns={"date": "timestamp"}, inplace=True)
                        elif "datetime" in sym_df.columns:
                            sym_df.rename(columns={"datetime": "timestamp"}, inplace=True)
                        
                        # Ensure timestamp is datetime and timezone-aware (UTC)
                        if "timestamp" in sym_df.columns:
                            sym_df["timestamp"] = pd.to_datetime(sym_df["timestamp"])
                            sym_df["timestamp"] = ensure_utc_series(sym_df["timestamp"])
                        
                        # Select and reorder columns
                        column_order = ["symbol", "timestamp", "open", "high", "low", "close", "volume"]
                        available_cols = [col for col in column_order if col in sym_df.columns]
                        sym_df = sym_df[available_cols]
                        
                        # Merge with existing DB data if any
                        db_data_for_symbol = self.get_data_from_db(symbol=symbol, start_date=start_date, end_date=end_date)
                        if not db_data_for_symbol.empty:
                            # Combine and remove duplicates
                            combined = pd.concat([db_data_for_symbol, sym_df], ignore_index=True)
                            combined = combined.drop_duplicates(subset=["timestamp"], keep="last")
                            combined = combined.sort_values("timestamp").reset_index(drop=True)
                            yf_data_list.append(combined)
                        else:
                            yf_data_list.append(sym_df)
                    else:
                        print(f"Warning: Symbol {symbol} not found in downloaded data, skipping")
                        # Use DB data if available
                        db_data_for_symbol = self.get_data_from_db(symbol=symbol, start_date=start_date, end_date=end_date)
                        if not db_data_for_symbol.empty:
                            yf_data_list.append(db_data_for_symbol)
                        continue
                except Exception as e:
                    print(f"Warning: Error processing symbol {symbol}: {e}, skipping")
                    # Use DB data if available
                    db_data_for_symbol = self.get_data_from_db(symbol=symbol, start_date=start_date, end_date=end_date)
                    if not db_data_for_symbol.empty:
                        yf_data_list.append(db_data_for_symbol)
                    continue
        
        # Combine DB data and yfinance data
        all_data_list = db_data_list + yf_data_list
        
        if not all_data_list:
            raise ValueError("No valid data found for any of the provided symbols")
        
        # Combine all symbols into one DataFrame
        symbol_data = pd.concat(all_data_list, ignore_index=True)
        
        # Rename columns to lowercase to match getYFData format
        symbol_data.columns = [col.lower() for col in symbol_data.columns]
        
        # Handle timestamp column - yfinance uses 'Date' or 'Datetime' as index name
        if "date" in symbol_data.columns:
            symbol_data.rename(columns={"date": "timestamp"}, inplace=True)
        elif "datetime" in symbol_data.columns:
            symbol_data.rename(columns={"datetime": "timestamp"}, inplace=True)
        elif "timestamp" not in symbol_data.columns:
            # Check if index has a name
            if symbol_data.index.name and symbol_data.index.name.lower() in ["date", "datetime"]:
                symbol_data = symbol_data.reset_index()
                symbol_data.rename(columns={symbol_data.columns[0]: "timestamp"}, inplace=True)
        
        # Ensure timestamp is datetime type and timezone-aware (UTC)
        if "timestamp" in symbol_data.columns:
            symbol_data["timestamp"] = pd.to_datetime(symbol_data["timestamp"])
            symbol_data["timestamp"] = ensure_utc_series(symbol_data["timestamp"])
        
        # Select and reorder columns to match expected format
        column_order = ["symbol", "timestamp", "open", "high", "low", "close", "volume"]
        # Only include columns that exist
        available_cols = [col for col in column_order if col in symbol_data.columns]
        symbol_data = symbol_data[available_cols]
        
        # Save each symbol's data to DB if requested
        # Only save symbols that were fetched from yfinance (they might have new data)
        if save_to_db and symbols_to_fetch:
            for symbol in symbols_to_fetch:
                # Get data for this symbol from the combined DataFrame
                symbol_df = symbol_data[symbol_data["symbol"] == symbol].copy()
                if symbol_df.empty:
                    continue
                # Ensure all required columns are present
                if not all(col in symbol_df.columns for col in REQUIRED_DATA_COLUMNS):
                    print(f"Warning: Missing required columns for {symbol}, skipping DB save")
                    continue
                
                # Check if there are actually new rows to save
                with get_db_session() as session:
                    latest = (
                        session.query(HistoricData.timestamp)
                        .filter_by(symbol=symbol)
                        .order_by(HistoricData.timestamp.desc())
                        .first()
                    )
                    if latest:
                        latest_ts = ensure_utc_timestamp(pd.Timestamp(latest[0]))
                        
                        # Filter to only rows newer than latest in DB
                        df_timestamps = ensure_utc_series(symbol_df["timestamp"].copy())
                        
                        new_rows = symbol_df[df_timestamps > latest_ts]
                        if new_rows.empty:
                            # No new rows, skip saving
                            continue
                        # Only save new rows
                        self.add_pd_df_to_db(new_rows)
                    else:
                        # No existing data, save all rows
                        self.add_pd_df_to_db(symbol_df)
        
        return symbol_data
    
    def convert_to_wide_format(
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
        if data_long.empty:
            return pd.DataFrame()
        
        # Convert to wide format: dates as index, symbols as columns
        df = data_long.pivot_table(
            index='timestamp',
            columns='symbol',
            values=value_column
        )
        
        # Handle missing values based on fill_method
        if fill_method == "forward" or fill_method == "both":
            df = df.ffill()
        if fill_method == "backward" or fill_method == "both":
            df = df.bfill()
        
        # Remove any columns that are all NaN
        df = df.dropna(axis=1, how='all')
        
        return df
    
    def add_pd_df_to_db(self, df: pd.DataFrame) -> None:
        """
        Add DataFrame rows to database, skipping duplicates.
        
        Only inserts rows with timestamps newer than the latest in database.
        
        Args:
            df: DataFrame with columns: symbol, timestamp, open, high, low, close, volume
        """
        validate_dataframe_columns(df)
        print("Adding only missing DataFrame rows to DB")
        symbol = df["symbol"].iloc[0]
        with get_db_session() as session:
            # Step 1: Get latest timestamp for symbol
            latest = (
                session.query(HistoricData.timestamp)
                .filter_by(symbol=symbol)
                .order_by(HistoricData.timestamp.desc())
                .first()
            )
            if latest:
                latest_ts = ensure_utc_timestamp(pd.Timestamp(latest[0]))
                
                # Ensure DataFrame timestamps are also timezone-aware (UTC)
                df_timestamps = ensure_utc_series(df["timestamp"].copy())
                
                # Step 2: Filter DataFrame to only new rows
                df_new = df[df_timestamps > latest_ts]
            else:
                df_new = df
            print(f"Rows to insert: {len(df_new)}")
            # Step 3: Insert only missing rows
            for _, row in df_new.iterrows():
                hd = HistoricData(
                    symbol=row["symbol"],
                    timestamp=row["timestamp"],
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
                session.merge(hd)
            # Context manager will commit automatically
    
    def get_latest_price(self, symbol: str, cached_data: Optional[pd.DataFrame] = None) -> float:
        """
        Get the latest price for a symbol, using TTL cache and checking DB first.
        
        Args:
            symbol: Trading symbol to get price for
            cached_data: Optional cached DataFrame to check first
            
        Returns:
            Latest price as float
            
        Raises:
            ValueError: If no price data is available
        """
        # Check TTL cache first
        cache_key = symbol
        if cache_key in _price_cache:
            return _price_cache[cache_key]
        
        # Check DB first
        now = pd.Timestamp.now(tz="UTC")
        with get_db_session() as session:
            latest = (
                session.query(HistoricData.close, HistoricData.timestamp)
                .filter_by(symbol=symbol)
                .order_by(HistoricData.timestamp.desc())
                .first()
            )
            
            if latest:
                latest_price = float(latest[0])
                latest_timestamp = ensure_utc_timestamp(pd.Timestamp(latest[1]))
                
                # Check if DB data is fresh enough (within tolerance)
                time_diff_minutes = (now - latest_timestamp).total_seconds() / 60
                if time_diff_minutes <= FRESHNESS_TOLERANCE_MINUTES:
                    # Cache and return
                    _price_cache[cache_key] = latest_price
                    return latest_price
        
        # Fallback to cached data if available and matches symbol
        if cached_data is not None and len(cached_data) > 0:
            if symbol in cached_data["symbol"].values:
                symbol_data = cached_data[cached_data["symbol"] == symbol]
                if len(symbol_data) > 0:
                    price = float(symbol_data["close"].iloc[-1])
                    _price_cache[cache_key] = price
                    return price
        
        # Last resort: fetch from yfinance
        ticker = yf.Ticker(symbol)
        price_data = ticker.history(period="1d", interval="1m")
        if price_data.empty:
            raise ValueError(f"No price data found for {symbol}")
        price = float(price_data["Close"].iloc[-1])
        
        # Save to DB via addPdDFToDb (create a minimal DataFrame)
        # Get the latest row from the history
        latest_row = price_data.iloc[-1]
        price_df = pd.DataFrame([{
            "symbol": symbol,
            "timestamp": price_data.index[-1],
            "open": float(latest_row["Open"]),
            "high": float(latest_row["High"]),
            "low": float(latest_row["Low"]),
            "close": float(latest_row["Close"]),
            "volume": float(latest_row["Volume"]),
        }])
        # Ensure timestamp is timezone-aware
        price_df["timestamp"] = ensure_utc_series(pd.to_datetime(price_df["timestamp"]))
        self.add_pd_df_to_db(price_df)
        
        # Cache and return
        _price_cache[cache_key] = price
        return price
    
    def get_latest_prices_batch(self, symbols: list[str]) -> dict[str, float]:
        """
        Get latest prices for multiple symbols in a single DB query.
        
        Args:
            symbols: List of trading symbols to get prices for
            
        Returns:
            Dictionary mapping symbol to latest price
        """
        if not symbols:
            return {}
        
        now = pd.Timestamp.now(tz="UTC")
        prices = {}
        
        with get_db_session() as session:
            # Query latest prices for all symbols at once
            # Use a subquery to get the latest timestamp for each symbol, then join
            from sqlalchemy import func
            
            subquery = (
                session.query(
                    HistoricData.symbol,
                    func.max(HistoricData.timestamp).label("max_timestamp")
                )
                .filter(HistoricData.symbol.in_(symbols))
                .group_by(HistoricData.symbol)
                .subquery()
            )
            
            results = (
                session.query(HistoricData.symbol, HistoricData.close, HistoricData.timestamp)
                .join(
                    subquery,
                    (HistoricData.symbol == subquery.c.symbol) &
                    (HistoricData.timestamp == subquery.c.max_timestamp)
                )
                .all()
            )
            
            for symbol, close, timestamp in results:
                # Check freshness
                latest_timestamp = ensure_utc_timestamp(pd.Timestamp(timestamp))
                
                time_diff_minutes = (now - latest_timestamp).total_seconds() / 60
                if time_diff_minutes <= FRESHNESS_TOLERANCE_MINUTES:
                    price = float(close)
                    prices[symbol] = price
                    # Also update cache
                    _price_cache[symbol] = price
        
        # For symbols not found in DB or with stale data, fallback to individual getLatestPrice
        for symbol in symbols:
            if symbol not in prices:
                try:
                    prices[symbol] = self.get_latest_price(symbol)
                except Exception as e:
                    print(f"Warning: Could not get price for {symbol}: {e}")
                    # Leave it out of the dict
        
        return prices

