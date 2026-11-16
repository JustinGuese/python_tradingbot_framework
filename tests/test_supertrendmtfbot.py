"""Tests for SupertrendMTFBot and compute_weights."""

from __future__ import annotations

import os

os.environ.setdefault("POSTGRES_URI", "postgresql://ci:ci@localhost:5432/ci")

import numpy as np
import pandas as pd
import pytest

from tradingbot.supertrendmtfbot import (
    HTF,
    MIN_ROWS,
    MULT,
    PERIOD,
    UNIVERSE,
    SupertrendMTFBot,
    compute_weights,
)
from tradingbot.utils.botclass import Bot


def _make_ohlcv(prices: list[float], start: str = "2023-01-01 00:00", freq: str = "1h") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(prices), freq=freq)
    return pd.DataFrame(
        {
            "open": prices,
            "high": [p + 1.0 for p in prices],
            "low": [p - 1.0 for p in prices],
            "close": [p + 0.5 for p in prices],
            "volume": 1000.0,
        },
        index=idx,
    )


def test_htf_minus_one_forces_flat_even_when_base_is_plus_one():
    """Test 1: HTF -1 forces flat even when base Supertrend is +1."""
    # 170 bars of downtrend (HTF -1) followed by 30 bars of sharp bounce (base +1)
    prices = [200.0 - i * 0.5 for i in range(170)]
    prices += [prices[-1] + i * 0.5 for i in range(30)]

    df = _make_ohlcv(prices)
    weights = compute_weights({"SPY": df}, period=10, mult=2.0, htf="1D")

    # Base is bullish (+1.0) but HTF is bearish (-1.0), so allocation must be flat
    assert weights == {}


def test_both_plus_one_allocates_one_fifth():
    """Test 2: Both +1 -> {ticker: 1/5}."""
    # 100 bars of monotonic uptrend ensures both base and HTF are +1
    prices = [100.0 + i * 0.5 for i in range(100)]
    df = _make_ohlcv(prices)

    weights = compute_weights({"SPY": df}, period=10, mult=2.0, htf="1D")
    assert weights == {"SPY": pytest.approx(0.2)}

    # Two active tickers each receive 1/5 (0.2)
    weights_two = compute_weights({"SPY": df, "QQQ": df}, period=10, mult=2.0, htf="1D")
    assert weights_two == {"SPY": pytest.approx(0.2), "QQQ": pytest.approx(0.2)}


def test_too_little_history_returns_empty():
    """Test 3: Too little history returns {}."""
    # Empty frames dict
    assert compute_weights({}) == {}

    # Frame with fewer rows than warmup (e.g. 5 rows < max(10, 10))
    short_df = _make_ohlcv([100.0, 101.0, 102.0, 103.0, 104.0])
    assert compute_weights({"SPY": short_df}, period=10, min_rows=10) == {}

    # None frame
    assert compute_weights({"SPY": None}) == {}

    # Empty DataFrame
    empty_df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    assert compute_weights({"SPY": empty_df}) == {}


def test_edge_cases_and_bounded_weights():
    """Edge cases: corrupted data, NaNs, and portfolio bounds."""
    # NaNs in prices
    prices = [100.0 + i * 0.5 for i in range(100)]
    prices[10] = np.nan
    df = _make_ohlcv(prices)

    # Should compute without raising
    weights = compute_weights({"SPY": df}, period=10, mult=2.0, htf="1D")
    assert all(w >= 0.0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9

    # Idle weight is cash (not in returned dict)
    assert "USD" not in weights


def test_bot_class_structure_and_metadata(monkeypatch):
    """Test Bot initialization, metadata, and targetWeights call."""
    assert SupertrendMTFBot.BACKTEST_PERIOD == "730d"
    assert SupertrendMTFBot.param_grid == {
        "period": [7, 10, 14],
        "mult": [2.0, 3.0, 4.0],
    }

    # Verify Bot initialization parameters
    captured: dict = {}

    def fake_init(self, name, **kwargs):
        captured["name"] = name
        captured.update(kwargs)
        self.tickers = kwargs.get("tickers", [])
        self.benchmark_tickers = []
        self.datas = {}

    monkeypatch.setattr(Bot, "__init__", fake_init)

    bot = SupertrendMTFBot()
    assert captured["name"] == "SupertrendMTFBot"
    assert captured["tickers"] == UNIVERSE
    assert captured["interval"] == "1h"
    assert captured["period"] == "60d"
    assert bot.indicator_period == PERIOD
    assert bot.mult == MULT
    assert bot.htf == HTF
    assert bot.min_rows == MIN_ROWS

    # Verify targetWeights delegates to compute_weights via self.datas
    prices = [100.0 + i * 0.5 for i in range(100)]
    df = _make_ohlcv(prices)
    bot.datas = {t: df for t in UNIVERSE}

    dummy_rows = {t: df.iloc[-1] for t in UNIVERSE}
    weights = bot.targetWeights(dummy_rows)
    assert len(weights) == 5
    for t in UNIVERSE:
        assert weights[t] == pytest.approx(0.2)
