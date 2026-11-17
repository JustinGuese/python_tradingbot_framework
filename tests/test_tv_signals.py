"""Unit tests for TradingView signals contract (Step S0 of PLAN-tvdaily.md).

Tests:
- UNIVERSE_WIDE list contents and constraints.
- signal_flags causality for all 4 TradingView trend signals.
- vote_weights ensemble allocation, per-ticker cap (0.2), gross_cap, empty cases, no USD.
- apply_min_hold hysteresis and causality.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils.tv_signals import (
    DEFAULT_PARAMS,
    SUPPORTED_KINDS,
    UNIVERSE_WIDE,
    apply_min_hold,
    signal_flags,
    vote_weights,
)


@pytest.fixture
def synthetic_ohlcv() -> pd.DataFrame:
    """Generate reproducible 120-bar synthetic daily OHLCV dataframe with cyclic motion."""
    np.random.seed(42)
    n = 120
    dates = pd.date_range("2020-01-01", periods=n, freq="D")
    cycle = 10.0 * np.sin(np.linspace(0, 4 * np.pi, n))
    noise = np.cumsum(np.random.randn(n) * 0.5)
    close = 100.0 + cycle + noise
    high = close + np.abs(np.random.randn(n) * 1.0) + 0.5
    low = close - np.abs(np.random.randn(n) * 1.0) - 0.5
    open_p = (high + low) / 2.0
    vol = np.random.uniform(1000, 5000, n)
    return pd.DataFrame(
        {"open": open_p, "high": high, "low": low, "close": close, "volume": vol},
        index=dates,
    )


# --- UNIVERSE_WIDE tests -----------------------------------------------------


def test_universe_wide_members() -> None:
    expected = [
        "SPY", "QQQ", "IWM", "EFA", "EEM", "XLE", "XLF", "XLK", "XLV", "XLP",
        "XLU", "GLD", "SLV", "DBC", "USO", "TLT", "IEF", "HYG", "UUP",
    ]
    assert UNIVERSE_WIDE == expected
    assert len(UNIVERSE_WIDE) == 19
    # No leveraged or inverse ETFs
    for ticker in UNIVERSE_WIDE:
        assert not any(x in ticker for x in ["2X", "3X", "SH", "PSQ", "DOG", "SDS"])


# --- signal_flags causality tests --------------------------------------------


@pytest.mark.parametrize("kind", SUPPORTED_KINDS)
def test_signal_flags_causality(synthetic_ohlcv: pd.DataFrame, kind: str) -> None:
    """Verify causality: future bars cannot alter past signals."""
    full_flags = signal_flags(synthetic_ohlcv, kind=kind)
    assert isinstance(full_flags, pd.Series)
    assert full_flags.dtype == bool
    assert len(full_flags) == len(synthetic_ohlcv)

    # Truncate at bar 70 and bar 95
    for cutoff in (70, 95):
        truncated_ohlcv = synthetic_ohlcv.iloc[:cutoff]
        truncated_flags = signal_flags(truncated_ohlcv, kind=kind)
        assert len(truncated_flags) == cutoff
        # Past flags must strictly match
        pd.testing.assert_series_equal(
            full_flags.iloc[:cutoff],
            truncated_flags,
            check_names=False,
        )


def test_signal_flags_empty_and_unknown() -> None:
    empty_df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    res = signal_flags(empty_df, kind="supertrend")
    assert len(res) == 0
    assert res.dtype == bool

    with pytest.raises(ValueError, match="Unknown signal kind"):
        signal_flags(empty_df, kind="non_existent_indicator")


def test_signal_flags_with_custom_params(synthetic_ohlcv: pd.DataFrame) -> None:
    flags_st_default = signal_flags(synthetic_ohlcv, kind="supertrend")
    flags_st_tight = signal_flags(synthetic_ohlcv, kind="supertrend", params={"period": 5, "mult": 1.5})
    assert isinstance(flags_st_tight, pd.Series)
    assert flags_st_tight.dtype == bool
    # Tight settings change signals relative to default
    assert not flags_st_tight.equals(flags_st_default)


# --- vote_weights tests ------------------------------------------------------


def test_vote_weights_empty_inputs() -> None:
    assert vote_weights({}, min_votes=1) == {}
    assert vote_weights({"SPY": {"st": True}}, min_votes=2) == {}
    assert vote_weights({"SPY": {}}, min_votes=1) == {}
    assert vote_weights({"SPY": {"st": True}}, min_votes=1, gross_cap=0.0) == {}
    assert vote_weights({"SPY": {"st": True}}, min_votes=1, gross_cap=-0.5) == {}


def test_vote_weights_per_ticker_cap() -> None:
    # 1 qualifying ticker: capped at 0.2 despite gross_cap=1.0
    flags = {
        "SPY": {"s1": True, "s2": True, "s3": False},
        "QQQ": {"s1": True, "s2": False, "s3": False},
    }
    w = vote_weights(flags, min_votes=2, gross_cap=1.0)
    assert w == {"SPY": 0.2}
    assert sum(w.values()) == pytest.approx(0.2)
    assert w["SPY"] <= 0.200001


def test_vote_weights_multiple_qualifying() -> None:
    flags = {
        f"ETF_{i}": {"s1": True, "s2": True} for i in range(10)
    }
    # 10 qualifying tickers with gross_cap=1.0 -> 0.1 each
    w = vote_weights(flags, min_votes=2, gross_cap=1.0)
    assert len(w) == 10
    for weight in w.values():
        assert weight == pytest.approx(0.1)
        assert weight <= 0.2
    assert sum(w.values()) == pytest.approx(1.0)

    # 3 qualifying tickers with gross_cap=1.0 -> 0.2 each (sum 0.6 <= 1.0)
    flags_3 = {f"ETF_{i}": {"s1": True, "s2": True} for i in range(3)}
    w_3 = vote_weights(flags_3, min_votes=2, gross_cap=1.0)
    assert len(w_3) == 3
    for weight in w_3.values():
        assert weight == pytest.approx(0.2)
    assert sum(w_3.values()) == pytest.approx(0.6)


def test_vote_weights_gross_cap_constraint() -> None:
    flags = {
        "SPY": {"s1": True},
        "QQQ": {"s1": True},
    }
    # gross_cap=0.3 -> each gets min(0.2, 0.3 / 2) = 0.15
    w = vote_weights(flags, min_votes=1, gross_cap=0.3)
    assert w == {"QQQ": pytest.approx(0.15), "SPY": pytest.approx(0.15)}
    assert sum(w.values()) <= 0.300001


def test_vote_weights_never_usd_key() -> None:
    flags = {
        "SPY": {"s1": True, "s2": True},
        "USD": {"s1": True, "s2": True},
    }
    w = vote_weights(flags, min_votes=2, gross_cap=1.0)
    assert "USD" not in w
    assert w == {"SPY": 0.2}


# --- apply_min_hold tests ----------------------------------------------------


def test_apply_min_hold_identity_and_empty() -> None:
    raw = pd.Series([True, False, True, False])
    assert apply_min_hold(raw, bars=0).tolist() == raw.tolist()
    assert apply_min_hold(raw, bars=1).tolist() == raw.tolist()
    assert len(apply_min_hold(pd.Series([], dtype=bool), bars=5)) == 0


def test_apply_min_hold_hysteresis() -> None:
    # State flips only allowed after >= bars elapsed
    raw = pd.Series([True, True, False, False, True, True, True, True])
    held = apply_min_hold(raw, bars=3)
    # Flip to False at index 2 suppressed until index 3. Flip to True at index 4 suppressed until index 6.
    assert held.tolist() == [True, True, True, False, False, False, True, True]


def test_apply_min_hold_causality() -> None:
    np.random.seed(99)
    raw = pd.Series(np.random.choice([True, False], size=100))
    full = apply_min_hold(raw, bars=5)
    for cutoff in (30, 60, 80):
        trunc = apply_min_hold(raw.iloc[:cutoff], bars=5)
        pd.testing.assert_series_equal(full.iloc[:cutoff], trunc)
