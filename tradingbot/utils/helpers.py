"""Utility functions for timezone handling and data validation."""

from typing import Optional

import pandas as pd

from .constants import REQUIRED_DATA_COLUMNS


def ensure_utc_timestamp(timestamp: pd.Timestamp) -> pd.Timestamp:
    """
    Ensure a timestamp is timezone-aware in UTC.
    
    Args:
        timestamp: Timestamp to convert (may be timezone-naive or in another timezone)
        
    Returns:
        Timezone-aware timestamp in UTC
    """
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    else:
        return timestamp.tz_convert("UTC")


def ensure_utc_series(series: pd.Series) -> pd.Series:
    """
    Ensure a pandas Series of timestamps is timezone-aware in UTC.
    
    Args:
        series: Series of timestamps to convert
        
    Returns:
        Series with timezone-aware timestamps in UTC
    """
    if series.dt.tz is None:
        return series.dt.tz_localize("UTC")
    else:
        return series.dt.tz_convert("UTC")


def validate_dataframe_columns(df: pd.DataFrame, required_columns: Optional[list[str]] = None) -> None:
    """
    Validate that a DataFrame has the required columns.
    
    Args:
        df: DataFrame to validate
        required_columns: List of required column names (defaults to REQUIRED_DATA_COLUMNS)
        
    Raises:
        AssertionError: If DataFrame doesn't have the required columns
    """
    if required_columns is None:
        required_columns = REQUIRED_DATA_COLUMNS
    
    assert isinstance(df, pd.DataFrame), "Input must be a pandas DataFrame"
    assert df.columns.tolist() == required_columns, (
        f'DataFrame must have specific columns: {required_columns}, got: {df.columns.tolist()}'
    )


def parse_period_to_date_range(period: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """
    Convert yfinance period string to start and end datetime range.
    
    Args:
        period: Period string (e.g., "1d", "5d", "1mo", "1y", "ytd", "max")
        
    Returns:
        Tuple of (start_date, end_date) in UTC timezone-aware timestamps
    """
    now = pd.Timestamp.now(tz="UTC")
    end_date = now
    
    # Handle special cases
    if period == "ytd":
        # Year to date - start of current year
        start_date = pd.Timestamp(year=now.year, month=1, day=1, tz="UTC")
    elif period == "max":
        # Maximum available data - use a very old date
        start_date = pd.Timestamp(year=1970, month=1, day=1, tz="UTC")
    else:
        # Parse numeric period strings
        try:
            # Extract number and unit
            if period.endswith("d"):
                days = int(period[:-1])
                start_date = now - pd.Timedelta(days=days)
            elif period.endswith("wk"):
                weeks = int(period[:-2])
                start_date = now - pd.Timedelta(weeks=weeks)
            elif period.endswith("mo"):
                months = int(period[:-2])
                start_date = now - pd.DateOffset(months=months)
            elif period.endswith("y"):
                years = int(period[:-1])
                start_date = now - pd.DateOffset(years=years)
            else:
                # Default to 1 day if parsing fails
                start_date = now - pd.Timedelta(days=1)
        except (ValueError, AttributeError):
            # Default to 1 day if parsing fails
            start_date = now - pd.Timedelta(days=1)
    
    return (start_date, end_date)

