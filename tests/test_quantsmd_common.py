import numpy as np
import pandas as pd
import pytest

from tradingbot.utils.quantsmd_common import (
    ETF8,
    SECTORS9,
    annualized_vol,
    closes_frame,
    equal_weight,
    is_weekday_bar,
    period_rolled,
)


def test_constants():
    assert len(ETF8) == 8
    assert len(SECTORS9) == 9


def test_period_rolled_month():
    idx = pd.DatetimeIndex(["2024-01-30", "2024-01-31", "2024-02-01"])
    assert period_rolled(idx, "M") is True
    idx = pd.DatetimeIndex(["2024-01-30", "2024-01-31"])
    assert period_rolled(idx, "M") is False


def test_period_rolled_quarter():
    idx = pd.DatetimeIndex(["2024-03-28", "2024-04-01"])
    assert period_rolled(idx, "Q") is True
    idx = pd.DatetimeIndex(["2024-04-01", "2024-04-02"])
    assert period_rolled(idx, "Q") is False


def test_period_rolled_short_and_invalid():
    assert period_rolled(pd.DatetimeIndex(["2024-01-31"]), "M") is False
    with pytest.raises(ValueError):
        period_rolled(pd.DatetimeIndex(["2024-01-30", "2024-01-31"]), "W")


def test_is_weekday_bar():
    idx = pd.DatetimeIndex(["2024-01-04", "2024-01-05"])  # Thu, Fri
    assert is_weekday_bar(idx, 4) is True
    assert is_weekday_bar(idx, 0) is False
    assert is_weekday_bar(pd.DatetimeIndex([]), 4) is False


def test_equal_weight():
    assert equal_weight(["a", "b"], 0.25) == {"a": 0.25, "b": 0.25}
    weights = equal_weight(["a", "b", "c", "d", "e"], 0.25)
    assert set(weights) == {"a", "b", "c", "d", "e"}
    for w in weights.values():
        assert w == pytest.approx(0.2)
    assert equal_weight([], 0.25) == {}


def test_closes_frame_mixed_inputs():
    idx = pd.date_range("2024-01-02", periods=5, freq="D")
    with_ts = pd.DataFrame(
        {
            "timestamp": idx[::-1],  # deliberately unsorted
            "close": [5.0, 4.0, 3.0, 2.0, 1.0],
        }
    )
    with_index = pd.DataFrame({"close": [10.0, 11.0, 12.0, 13.0, 14.0]}, index=idx)
    empty = pd.DataFrame()
    datas = {"A": with_ts, "B": with_index, "C": empty, "D": None}
    out = closes_frame(datas, ["A", "B", "C", "D"])
    assert list(out.columns) == ["A", "B"]
    assert out.index.is_monotonic_increasing
    assert len(out) == 5
    assert out["A"].iloc[0] == 1.0
    assert out["B"].iloc[-1] == 14.0


def test_closes_frame_empty_when_nothing_found():
    assert closes_frame({}, ["X"]).empty


def test_closes_frame_tz_aware_becomes_naive():
    idx = pd.date_range("2024-01-02", periods=3, freq="D", tz="America/New_York")
    frame = pd.DataFrame({"close": [1.0, 2.0, 3.0]}, index=idx)
    out = closes_frame({"A": frame}, ["A"])
    assert out.index.tz is None


def test_annualized_vol_constant_growth_is_zero():
    closes = pd.DataFrame({"A": 100.0 * (1.01 ** np.arange(100))})
    vol = annualized_vol(closes, 20)
    assert abs(vol["A"]) < 1e-9


def test_annualized_vol_too_short_is_nan():
    closes = pd.DataFrame({"A": [100.0, 101.0, 102.0, 103.0]})
    vol = annualized_vol(closes, 10)
    assert np.isnan(vol["A"])
