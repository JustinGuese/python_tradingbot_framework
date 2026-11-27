"""Unit tests for TS151 stock strategies and signals (PLAN-ts151.md S4)."""

import numpy as np
import pandas as pd
import pytest

from tradingbot.ts151_alphacombobot import TS151AlphaComboBot
from tradingbot.ts151_channelbot import TS151ChannelBot
from tradingbot.ts151_distressbot import TS151DistressBot
from tradingbot.ts151_earningsmomentumbot import TS151EarningsMomentumBot
from tradingbot.ts151_impliedvolatilitybot import TS151ImpliedVolatilityBot
from tradingbot.ts151_knnbot import TS151KNNBot
from tradingbot.ts151_lowvolatilitybot import TS151LowVolatilityBot
from tradingbot.ts151_meanreversionclusterbot import TS151MeanReversionClusterBot
from tradingbot.ts151_mergerarbbot import TS151MergerArbBot
from tradingbot.ts151_multifactorbot import TS151MultifactorBot
from tradingbot.ts151_pairstradingbot import TS151PairsTradingBot
from tradingbot.ts151_pricemomentumbot import TS151PriceMomentumBot
from tradingbot.ts151_residualmomentumbot import TS151ResidualMomentumBot
from tradingbot.ts151_singlemabot import TS151SingleMABot
from tradingbot.ts151_statarbbot import TS151StatArbBot
from tradingbot.ts151_supportresistancebot import TS151SupportResistanceBot
from tradingbot.ts151_threemabot import TS151ThreeMABot
from tradingbot.ts151_twomabot import TS151TwoMABot
from tradingbot.ts151_valuebot import TS151ValueBot
from tradingbot.ts151_weightedregressionbot import TS151WeightedRegressionBot
from tradingbot.utils.ts151.base import TS151Bot
from tradingbot.utils.ts151.signals_stocks import (
    check_merger_heartbeat,
    check_sec_fundamentals_staleness,
    check_vol_surface_staleness,
    compute_channel_weights,
    compute_low_volatility_weights,
    compute_merger_arb_weights,
    compute_price_momentum_weights,
    compute_single_ma_weights,
    compute_support_resistance_weights,
    compute_three_ma_weights,
    compute_two_ma_weights,
)

STOCK_BOT_CLASSES = [
    TS151PriceMomentumBot,
    TS151EarningsMomentumBot,
    TS151ValueBot,
    TS151LowVolatilityBot,
    TS151ImpliedVolatilityBot,
    TS151MultifactorBot,
    TS151ResidualMomentumBot,
    TS151PairsTradingBot,
    TS151MeanReversionClusterBot,
    TS151WeightedRegressionBot,
    TS151SingleMABot,
    TS151TwoMABot,
    TS151ThreeMABot,
    TS151SupportResistanceBot,
    TS151ChannelBot,
    TS151MergerArbBot,
    TS151KNNBot,
    TS151StatArbBot,
    TS151AlphaComboBot,
    TS151DistressBot,
]


def test_stock_bots_class_contracts():
    """Verify all 20 stock bots inherit from TS151Bot and have valid configs."""
    assert len(STOCK_BOT_CLASSES) == 20

    for cls in STOCK_BOT_CLASSES:
        assert issubclass(cls, TS151Bot)
        assert hasattr(cls, "BOT_NAME") and isinstance(cls.BOT_NAME, str) and len(cls.BOT_NAME) > 0
        assert hasattr(cls, "DEFAULT_UNIVERSE")
        assert hasattr(cls, "ALLOW_SHORT")
        assert hasattr(cls, "MAX_GROSS")
        assert hasattr(cls, "DEFAULTS") and isinstance(cls.DEFAULTS, dict)
        assert hasattr(cls, "param_grid") and isinstance(cls.param_grid, dict)

        # Every key in DEFAULTS should appear in param_grid (or have alternatives)
        for k in cls.DEFAULTS:
            assert k in cls.param_grid, f"{cls.__name__}: default key {k} missing from param_grid"


def test_signals_price_momentum():
    """Verify price momentum signals."""
    dates = pd.date_range("2020-01-01", periods=300, freq="B")
    np.random.seed(42)
    tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA"]

    # AAPL has strong upward drift, META has strong downward drift
    data = {}
    for t in tickers:
        drift = 0.002 if t == "AAPL" else (-0.002 if t == "META" else 0.0)
        rets = np.random.normal(drift, 0.01, size=len(dates))
        data[t] = 100.0 * np.cumprod(1.0 + rets)

    closes = pd.DataFrame(data, index=dates)

    weights = compute_price_momentum_weights(closes, window=252, skip=21, frac=0.33, gross=2.0)
    assert weights is not None
    assert "AAPL" in weights and weights["AAPL"] > 0  # top performer is long
    assert "META" in weights and weights["META"] < 0  # worst performer is short
    assert abs(sum(weights.values())) < 1e-6  # dollar-neutral
    assert abs(sum(abs(w) for w in weights.values()) - 2.0) < 1e-6  # gross 2.0


def test_signals_low_volatility():
    """Verify low-volatility signals."""
    dates = pd.date_range("2020-01-01", periods=300, freq="B")
    np.random.seed(42)
    # A has very low vol (0.002), B has very high vol (0.05)
    data = {
        "LOW": 100.0 * np.cumprod(1.0 + np.random.normal(0.0001, 0.002, size=len(dates))),
        "MID1": 100.0 * np.cumprod(1.0 + np.random.normal(0.0001, 0.01, size=len(dates))),
        "MID2": 100.0 * np.cumprod(1.0 + np.random.normal(0.0001, 0.015, size=len(dates))),
        "HIGH": 100.0 * np.cumprod(1.0 + np.random.normal(0.0001, 0.05, size=len(dates))),
    }
    closes = pd.DataFrame(data, index=dates)

    weights = compute_low_volatility_weights(closes, window=252, frac=0.25, gross=2.0)
    assert weights is not None
    assert weights["LOW"] > 0
    assert weights["HIGH"] < 0
    assert abs(sum(weights.values())) < 1e-6


def test_signals_single_two_three_ma():
    """Verify MA strategies on synthetic closes."""
    dates = pd.date_range("2020-01-01", periods=250, freq="B")
    # Strong upward trend
    closes_up = pd.DataFrame({"SPY": np.linspace(100, 200, len(dates))}, index=dates)
    # Strong downward trend
    closes_down = pd.DataFrame({"SPY": np.linspace(200, 100, len(dates))}, index=dates)

    # 1-MA
    w_up = compute_single_ma_weights(closes_up, ticker="SPY", window=50, gross=1.0)
    w_down = compute_single_ma_weights(closes_down, ticker="SPY", window=50, gross=1.0)
    assert w_up == {"SPY": 1.0}
    assert w_down == {"SPY": -1.0}

    # 2-MA
    w2_up = compute_two_ma_weights(closes_up, ticker="SPY", fast=20, slow=50, gross=1.0)
    w2_down = compute_two_ma_weights(closes_down, ticker="SPY", fast=20, slow=50, gross=1.0)
    assert w2_up == {"SPY": 1.0}
    assert w2_down == {"SPY": -1.0}

    # 3-MA
    w3_up = compute_three_ma_weights(closes_up, ticker="SPY", s1=3, s2=10, s3=21, gross=1.0)
    w3_down = compute_three_ma_weights(closes_down, ticker="SPY", s1=3, s2=10, s3=21, gross=1.0)
    assert w3_up == {"SPY": 1.0}
    assert w3_down == {"SPY": -1.0}


def test_signals_channel_and_pivot():
    """Verify channel mean reversion and pivot point strategies."""
    dates = pd.date_range("2020-01-01", periods=50, freq="B")
    # Stable oscillating price
    px = [100.0] * 30 + [90.0]  # sharp drop on last bar
    closes = pd.DataFrame({"SPY": px}, index=dates[:31])

    # Channel buy at low
    w_ch = compute_channel_weights(closes, ticker="SPY", window=20, gross=1.0)
    assert w_ch == {"SPY": 1.0}

    # Pivot point
    datas = {
        "SPY": pd.DataFrame({
            "high": [105.0, 106.0],
            "low": [95.0, 96.0],
            "close": [100.0, 104.0],  # previous C = 100.0, current close = 104 > C
        })
    }
    w_pivot = compute_support_resistance_weights(datas, ticker="SPY", gross=1.0)
    assert w_pivot == {"SPY": 1.0}


def test_signals_merger_arb():
    """Verify merger arb signal allocates equal weight to recent merger targets."""
    as_of = pd.Timestamp("2026-05-01")
    events = pd.DataFrame([
        {
            "accession": "acc1",
            "ticker": "TARGET1",
            "filed_at": pd.Timestamp("2026-04-15"),
        },
        {
            "accession": "acc2",
            "ticker": "TARGET2",
            "filed_at": pd.Timestamp("2026-04-20"),
        },
        {
            "accession": "acc3",
            "ticker": "OLD_TARGET",
            "filed_at": pd.Timestamp("2025-01-01"),  # older than 120 days
        },
        {
            "accession": "_HEARTBEAT",
            "ticker": None,
            "filed_at": pd.Timestamp("2026-04-30"),
        },
    ])

    w = compute_merger_arb_weights(events, as_of=as_of, hold_bars=120, max_weight=0.1)
    assert "TARGET1" in w and "TARGET2" in w
    assert "OLD_TARGET" not in w
    assert w["TARGET1"] == 0.1
    assert w["TARGET2"] == 0.1


def test_staleness_checkers():
    """Verify that staleness checkers raise RuntimeError when thresholds are violated."""
    as_of = pd.Timestamp.now(tz="UTC")

    # Empty fundamentals
    with pytest.raises(RuntimeError, match="SEC fundamentals data missing"):
        check_sec_fundamentals_staleness(pd.DataFrame(), ["AAPL", "MSFT"], as_of)

    # Empty vol_surface
    with pytest.raises(RuntimeError, match="vol_surface data missing"):
        check_vol_surface_staleness(pd.DataFrame(), as_of)

    # Missing merger heartbeat
    merger_no_hb = pd.DataFrame([{"accession": "x", "filed_at": as_of}])
    with pytest.raises(RuntimeError, match="heartbeat"):
        check_merger_heartbeat(merger_no_hb, as_of)

    # Stale merger heartbeat (> 3 days)
    merger_stale_hb = pd.DataFrame([{"accession": "_HEARTBEAT", "filed_at": as_of - pd.Timedelta(days=10)}])
    with pytest.raises(RuntimeError, match="stale"):
        check_merger_heartbeat(merger_stale_hb, as_of)
