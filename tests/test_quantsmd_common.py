import numpy as np
import pandas as pd
import pytest

from tradingbot.utils.quantsmd_common import (
    ETF8,
    SECTORS9,
    annualized_vol,
    atr,
    closes_frame,
    equal_weight,
    erc_weights,
    is_weekday_bar,
    last_rows,
    period_rolled,
    rsi,
    rsi_hysteresis,
    scale_to_max_gross,
    top_n,
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


def test_rsi_hysteresis_enter_exit_and_nan():
    assert rsi_hysteresis(pd.Series([50.0, 25.0, 40.0]), 30.0, 70.0) is True
    assert rsi_hysteresis(pd.Series([50.0, 25.0, 75.0]), 30.0, 70.0) is False
    # Entry needs strictly below the threshold.
    assert rsi_hysteresis(pd.Series([30.0, 50.0]), 30.0, 70.0) is False
    # A NaN resets the state to flat.
    assert rsi_hysteresis(pd.Series([25.0, np.nan]), 30.0, 70.0) is False
    assert rsi_hysteresis(pd.Series([25.0, np.nan, 40.0]), 30.0, 70.0) is False


def test_top_n_positive_filter_and_order():
    scores = pd.Series({"A": 0.5, "B": -0.2, "C": 1.5, "D": np.nan, "E": 0.9})
    assert top_n(scores, 2) == ["C", "E"]
    assert top_n(scores, 4) == ["C", "E", "A", "B"]
    assert top_n(scores, 4, positive_only=True) == ["C", "E", "A"]


def test_scale_to_max_gross_scales_and_drops_non_positive():
    out = scale_to_max_gross({"A": 0.6, "B": 0.6, "C": 0.0, "D": -0.1})
    assert set(out) == {"A", "B"}
    assert out["A"] == pytest.approx(0.5)
    assert out["B"] == pytest.approx(0.5)
    under = scale_to_max_gross({"A": 0.3})
    assert set(under) == {"A"}
    assert under["A"] == pytest.approx(0.3)


def test_erc_weights_equal_vol_uncorrelated_is_equal():
    w = erc_weights(np.eye(4) * 0.04)
    assert w.sum() == pytest.approx(1.0)
    assert np.allclose(w, 0.25)


def test_atr_constant_range_equals_range():
    close = pd.Series([100.0] * 30)
    high = close + 1.0
    low = close - 1.0
    out = atr(high, low, close, window=20)
    assert out.iloc[:19].isna().all()
    assert out.iloc[-1] == pytest.approx(2.0)


def test_rsi_strictly_rising_is_high():
    close = pd.Series(np.arange(1.0, 61.0))
    out = rsi(close, 14)
    assert out.iloc[-1] > 90


def test_last_rows_length():
    df = pd.DataFrame({"x": range(10)})
    assert len(last_rows(df, 3)) == 3
    assert list(last_rows(df, 3)["x"]) == [7, 8, 9]
    assert len(last_rows(df, 50)) == 10
