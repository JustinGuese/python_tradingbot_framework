"""Tests for TradingView TA core primitives and HTF alignment helpers."""

from __future__ import annotations

import os

os.environ.setdefault("POSTGRES_URI", "postgresql://ci:ci@localhost:5432/ci")

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils.tv_core import (
    active_weights,
    align_htf,
    atr,
    crossover,
    crossunder,
    ema,
    hma,
    mfi,
    resample_ohlcv,
    rma,
    rsi,
    sma,
    t3,
    tema,
    to_ohlcv,
    true_range,
    wma,
)


def test_align_htf_no_lookahead():
    """align_htf has no look-ahead:

    1. A 1h series resampled to 4h: the HTF value visible at base bar t equals the
       HTF bar that closed at or before t.
    2. Changing later base bars never alters earlier aligned output.
    """
    idx = pd.date_range("2023-01-01 00:00", "2023-01-02 12:00", freq="1h")
    # Base bars have values equal to their integer hour index
    df = pd.DataFrame(
        {
            "open": np.arange(len(idx), dtype=float),
            "high": np.arange(len(idx), dtype=float) + 0.5,
            "low": np.arange(len(idx), dtype=float) - 0.5,
            "close": np.arange(len(idx), dtype=float),
            "volume": np.ones(len(idx), dtype=float) * 10.0,
        },
        index=idx,
    )

    htf = resample_ohlcv(df, "4h")
    # In 4h resampling with label='left', 00:00 covers 00:00-03:00 (last close = 3.0)
    # 04:00 covers 04:00-07:00 (last close = 7.0), etc.
    aligned = align_htf(htf["close"], df.index, "4h")

    # Bars 00:00 to 03:00: no 4h bar has closed yet -> must be NaN
    assert aligned.loc["2023-01-01 00:00:00":"2023-01-01 03:00:00"].isna().all()

    # Bar 04:00 to 07:00: 4h bar 00:00 closed at 04:00 -> value is 3.0
    assert (aligned.loc["2023-01-01 04:00:00":"2023-01-01 07:00:00"] == 3.0).all()

    # Bar 08:00 to 11:00: 4h bar 04:00 closed at 08:00 -> value is 7.0
    assert (aligned.loc["2023-01-01 08:00:00":"2023-01-01 11:00:00"] == 7.0).all()

    # Invariance to future changes: alter later base bars (e.g. from 12:00 onwards)
    df_modified = df.copy()
    df_modified.loc["2023-01-01 12:00:00":, "close"] += 500.0
    htf_modified = resample_ohlcv(df_modified, "4h")
    aligned_modified = align_htf(htf_modified["close"], df_modified.index, "4h")

    # Output prior to 16:00 (when 12:00-15:00 bar finishes) must be bit-for-bit identical
    pd.testing.assert_series_equal(
        aligned.loc[:"2023-01-01 15:00:00"],
        aligned_modified.loc[:"2023-01-01 15:00:00"],
    )


def test_atr_hand_computed_wilder_values():
    """atr on a hand-computed 5-bar series equals the Wilder value.

    Wilder RMA seed behaviour:
    Pandas ewm(alpha=1/n, adjust=False) sets y_0 = x_0 (first true range).
    Subsequent values follow Wilder recursive smoothing:
      y_t = (1 - alpha) * y_{t-1} + alpha * x_t with alpha = 1 / n.

    5-bar series with n = 3:
      Bar 0: H=10, L=8, C=9   -> prev_C=NaN -> TR0 = 10 - 8 = 2.0
      Bar 1: H=11, L=9, C=10  -> prev_C=9   -> TR1 = max(2, |11-9|, |9-9|) = 2.0
      Bar 2: H=13, L=10, C=12 -> prev_C=10  -> TR2 = max(3, |13-10|, |10-10|) = 3.0
      Bar 3: H=14, L=11, C=11 -> prev_C=12  -> TR3 = max(3, |14-12|, |11-12|) = 3.0
      Bar 4: H=15, L=10, C=14 -> prev_C=11  -> TR4 = max(5, |15-11|, |10-11|) = 5.0

    RMA with alpha = 1/3:
      y_0 = 2.0
      y_1 = (2/3)*2.0 + (1/3)*2.0 = 2.0
      y_2 = (2/3)*2.0 + (1/3)*3.0 = 7/3 (~2.333333)
      y_3 = (2/3)*(7/3) + (1/3)*3.0 = 23/9 (~2.555556)
      y_4 = (2/3)*(23/9) + (1/3)*5.0 = 91/27 (~3.370370)
    """
    idx = pd.date_range("2023-01-01", periods=5, freq="D")
    df = pd.DataFrame(
        {
            "open": [9.0, 9.5, 11.0, 12.0, 12.0],
            "high": [10.0, 11.0, 13.0, 14.0, 15.0],
            "low": [8.0, 9.0, 10.0, 11.0, 10.0],
            "close": [9.0, 10.0, 12.0, 11.0, 14.0],
            "volume": [100.0] * 5,
        },
        index=idx,
    )

    tr = true_range(df)
    expected_tr = [2.0, 2.0, 3.0, 3.0, 5.0]
    np.testing.assert_allclose(tr.to_numpy(), expected_tr)

    atr_vals = atr(df, n=3)
    expected_atr = [2.0, 2.0, 7.0 / 3.0, 23.0 / 9.0, 91.0 / 27.0]
    np.testing.assert_allclose(atr_vals.to_numpy(), expected_atr)


def test_to_ohlcv_semantics():
    """to_ohlcv handles timestamp column and tz-aware input, dedupes, and fills missing volume with 0."""
    raw = pd.DataFrame(
        {
            "timestamp": [
                "2023-01-01 10:00:00+00:00",
                "2023-01-01 09:00:00+00:00",
                "2023-01-01 09:00:00+00:00",  # duplicate, should keep last
                "2023-01-01 12:00:00+00:00",  # NaN close, should be dropped
                "2023-01-01 11:00:00+00:00",
            ],
            "Open": [100.0, 90.0, 92.0, 110.0, 105.0],
            "High": [102.0, 95.0, 96.0, 115.0, 108.0],
            "Low": [99.0, 89.0, 91.0, 109.0, 104.0],
            "Close": [101.0, 93.0, 94.0, np.nan, 106.0],
            # Volume column intentionally omitted to test zero-fill
        }
    )

    res = to_ohlcv(raw)

    # 1. Output index is DatetimeIndex, tz-naive, ascending
    assert isinstance(res.index, pd.DatetimeIndex)
    assert res.index.tz is None
    assert res.index.is_monotonic_increasing

    # 2. Rows: 09:00 deduplicated (kept last: Close 94), 10:00 kept, 11:00 kept, 12:00 dropped (NaN)
    assert len(res) == 3
    assert res.loc["2023-01-01 09:00:00", "close"] == 94.0
    assert res.loc["2023-01-01 09:00:00", "open"] == 92.0

    # 3. Volume is filled with 0.0
    assert (res["volume"] == 0.0).all()

    # 4. Column types are float
    assert all(res[col].dtype == float for col in ["open", "high", "low", "close", "volume"])


def test_moving_averages_and_indicators():
    """Verify core moving averages (sma, ema, wma, hma, tema, t3), rsi, and mfi."""
    idx = pd.date_range("2023-01-01", periods=60, freq="1h")
    # Linear ramp
    s = pd.Series(np.arange(60, dtype=float), index=idx)

    # All MAs return finite values on ramp and preserve index
    assert not sma(s, 10).dropna().empty
    assert not ema(s, 10).dropna().empty
    assert not wma(s, 10).dropna().empty
    assert not hma(s, 10).dropna().empty
    assert not tema(s, 10).dropna().empty
    assert not t3(s, 10, vfactor=0.7).dropna().empty

    # RSI on monotonically increasing series is 100
    r_up = rsi(s, 14)
    assert r_up.iloc[-1] == 100.0

    # RSI on monotonically decreasing series is 0
    s_down = pd.Series(np.arange(60, 0, -1, dtype=float), index=idx)
    r_down = rsi(s_down, 14)
    assert r_down.iloc[-1] == 0.0

    # MFI with zero volume returns all NaN
    df_zero_vol = pd.DataFrame(
        {"open": s, "high": s + 1, "low": s - 1, "close": s, "volume": 0.0},
        index=idx,
    )
    assert mfi(df_zero_vol, 14).isna().all()

    # MFI with non-zero volume produces valid values
    df_vol = df_zero_vol.copy()
    df_vol["volume"] = 100.0
    mfi_val = mfi(df_vol, 14)
    assert not mfi_val.dropna().empty


def test_crossover_crossunder_strict():
    """crossover and crossunder are strict and False on the first bar."""
    idx = pd.date_range("2023-01-01", periods=5, freq="D")
    a = pd.Series([10.0, 20.0, 25.0, 15.0, 10.0], index=idx)
    b = pd.Series([15.0, 15.0, 15.0, 15.0, 15.0], index=idx)

    co = crossover(a, b)
    cu = crossunder(a, b)

    # First bar must always be False
    assert not co.iloc[0]
    assert not cu.iloc[0]

    # Bar 1: a crossed over b (10->20 vs 15->15)
    assert co.iloc[1]
    # Bar 2: a remained above b (no crossover)
    assert not co.iloc[2]

    # Bar 3: a crossed from 25 to 15 (a == b, not < b, so no crossunder yet)
    assert not cu.iloc[3]
    # Bar 4: a crossed from 15 to 10 (< 15), crossunder fires!
    assert cu.iloc[4]


def test_active_weights_and_edge_cases():
    """active_weights divides 1/n_universe evenly among True flags, handles edge cases."""
    flags = {"QQQ": True, "SPY": True, "IWM": False, "GLD": False, "TLT": False}
    weights = active_weights(flags, n_universe=5)
    assert weights == {"QQQ": 0.2, "SPY": 0.2}

    # All False returns {}
    assert active_weights({"QQQ": False}, n_universe=5) == {}

    # Invalid period or universe raises ValueError
    with pytest.raises(ValueError, match="n_universe must be positive"):
        active_weights(flags, n_universe=0)

    with pytest.raises(ValueError, match="period n must be positive"):
        sma(pd.Series([1.0, 2.0]), 0)

    with pytest.raises(ValueError, match="period n must be at least 2"):
        hma(pd.Series([1.0, 2.0]), 1)


def test_to_ohlcv_keeps_volume_column_and_zero_fills_nan():
    frame = pd.DataFrame(
        {
            "timestamp": pd.date_range("2024-01-01", periods=3, tz="UTC"),
            "open": [1, 2, 3],
            "high": [1, 2, 3],
            "low": [1, 2, 3],
            "close": [1, 2, 3],
            "volume": [5, None, 7],
        }
    )
    assert to_ohlcv(frame)["volume"].tolist() == [5.0, 0.0, 7.0]
