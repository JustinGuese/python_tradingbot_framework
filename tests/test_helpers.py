import pytest
import pandas as pd
from datetime import datetime, timezone
from tradingbot.utils.helpers import (
    ensure_utc_timestamp,
    ensure_utc_series,
    parse_period_to_date_range,
)


def test_ensure_utc_timestamp_naive():
    naive_dt = datetime(2023, 1, 1, 12, 0, 0)
    utc_ts = ensure_utc_timestamp(pd.Timestamp(naive_dt))
    assert utc_ts.tzinfo is not None
    assert utc_ts.tzinfo == timezone.utc


def test_ensure_utc_timestamp_aware():
    aware_dt = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    utc_ts = ensure_utc_timestamp(pd.Timestamp(aware_dt))
    assert utc_ts == aware_dt


def test_ensure_utc_series():
    series = pd.Series([
        datetime(2023, 1, 1),
        datetime(2023, 1, 2, tzinfo=timezone.utc)
    ])
    utc_series = ensure_utc_series(series)
    for ts in utc_series:
        assert ts.tzinfo == timezone.utc


def test_parse_period_to_date_range():
    start, end = parse_period_to_date_range("1d")
    assert isinstance(start, pd.Timestamp)
    assert isinstance(end, pd.Timestamp)
    assert (end - start).days >= 1
    assert start.tzinfo == timezone.utc
    assert end.tzinfo == timezone.utc
