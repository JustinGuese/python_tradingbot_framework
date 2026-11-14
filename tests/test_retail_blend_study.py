"""Pins the two pieces of scripts/onetime_retail_blend_study.py that decide the verdict."""

import numpy as np
import pandas as pd
import pytest

import scripts.onetime_retail_blend_study as study


def _two_streams() -> pd.DataFrame:
    idx = pd.bdate_range("2021-01-01", "2023-12-29")
    rng = np.random.default_rng(0)
    df = pd.DataFrame(
        {"a": rng.normal(0, 0.01, len(idx)), "b": rng.normal(0, 0.02, len(idx))},
        index=idx,
    )
    return df


def test_inverse_vol_weights_sum_to_one_and_follow_h1_vol():
    df = _two_streams()
    w = study.inverse_vol_weights(df, "2023-01-01")
    assert sum(w.values()) == pytest.approx(1.0)
    h1 = df[df.index < "2023-01-01"]
    inv = 1 / h1.std()
    assert w["a"] == pytest.approx(inv["a"] / inv.sum())
    assert w["a"] > w["b"]  # the calmer stream gets more weight


def test_inverse_vol_weights_ignore_h2_data():
    df = _two_streams()
    base = study.inverse_vol_weights(df, "2023-01-01")
    shocked = df.copy()
    shocked.loc[shocked.index >= "2023-01-01", "a"] *= 50  # wild H2 only
    shocked.loc["2023-01-02", "b"] = 5.0
    assert study.inverse_vol_weights(shocked, "2023-01-01") == pytest.approx(base)


def test_gated_qqq_is_cash_below_sma_and_uses_previous_close_signal():
    idx = pd.bdate_range("2020-01-01", periods=12)
    # Rises, then drops hard below the 3-bar SMA on bar 6, then recovers.
    close = pd.Series([100, 101, 102, 103, 104, 105, 90, 80, 85, 95, 110, 120], index=idx, dtype=float)
    g = study.gated_qqq(close, window=3)
    sma = close.rolling(3).mean()

    # Warm-up: no SMA yet -> no signal, cash.
    assert g["signal"].iloc[:2].eq(0).all()
    # Signal at close t is exactly close_t > SMA_t.
    assert (g["signal"].iloc[2:] == (close > sma).iloc[2:].astype(float)).all()
    # Exposure during bar t is the signal from close t-1 (no look-ahead).
    assert (g["exposure"].iloc[1:].to_numpy() == g["signal"].iloc[:-1].to_numpy()).all()

    r = close.pct_change().fillna(0.0)
    # Bar 6 crashes: the signal at close 5 was "in", so the crash is taken in full.
    assert g["ret"].iloc[6] == pytest.approx(r.iloc[6])
    # Close 6 is below the SMA -> bar 7 is in cash and earns exactly 0.
    assert close.iloc[6] < sma.iloc[6]
    assert g["exposure"].iloc[7] == 0.0
    assert g["ret"].iloc[7] == 0.0
    # Whenever the prior close was below its SMA the bar earns nothing.
    below_prev = (close < sma).shift(1).fillna(True).astype(bool)
    assert g["ret"][below_prev].eq(0.0).all()


def test_gated_qqq_tbill_column_pays_only_on_cash_bars():
    idx = pd.bdate_range("2020-01-01", periods=12)
    close = pd.Series([100, 101, 102, 103, 104, 105, 90, 80, 85, 95, 110, 120], index=idx, dtype=float)
    rf = pd.Series(0.0001, index=idx)
    g = study.gated_qqq(close, window=3, rf_daily=rf)
    extra = g["ret_tbill"] - g["ret"]
    assert extra[g["exposure"] == 0].eq(0.0001).all()
    assert extra[g["exposure"] == 1].eq(0.0).all()


def test_changing_a_future_close_does_not_change_past_exposure():
    idx = pd.bdate_range("2020-01-01", periods=12)
    close = pd.Series([100, 101, 102, 103, 104, 105, 90, 80, 85, 95, 110, 120], index=idx, dtype=float)
    a = study.gated_qqq(close, window=3)
    bumped = close.copy()
    bumped.iloc[9] = 10.0
    b = study.gated_qqq(bumped, window=3)
    # Exposure through bar 9 depends only on closes up to bar 8.
    assert (a["exposure"].iloc[:10] == b["exposure"].iloc[:10]).all()
