import numpy as np
import pandas as pd
import pytest

from tradingbot.alphatrendbot import (
    DEFAULT_MULT,
    DEFAULT_PERIOD,
    UNIVERSE,
    AlphaTrendBot,
    compute_weights,
)
from tradingbot.utils.botclass import Bot


def _make_ohlcv(prices: np.ndarray, start: str = "2026-01-01 09:30:00") -> pd.DataFrame:
    n = len(prices)
    index = pd.date_range(start, periods=n, freq="1h")
    return pd.DataFrame(
        {
            "open": prices,
            "high": prices + 1.0,
            "low": prices - 1.0,
            "close": prices,
            "volume": 1000.0,
        },
        index=index,
    )


def test_replay_buy_held_for_later_bars_sell_flat():
    """Test 1: Replay state machine: buy -> held for later bars -> sell -> flat."""
    prices = np.concatenate(
        [
            np.linspace(100.0, 80.0, 25),  # downtrend: no buy
            np.linspace(80.0, 120.0, 20),  # rally: buy triggers around bar 32
            np.linspace(120.0, 70.0, 15),  # sharp drop: sell triggers around bar 52
        ]
    )
    df = _make_ohlcv(prices)

    # 1. Before buy signal: flat
    w_pre = compute_weights({"QQQ": df.iloc[:30]}, DEFAULT_PERIOD, DEFAULT_MULT)
    assert w_pre == {}

    # 2. On buy signal (around bar 33): active long
    w_buy = compute_weights({"QQQ": df.iloc[:33]}, DEFAULT_PERIOD, DEFAULT_MULT)
    assert w_buy == {"QQQ": pytest.approx(0.2)}

    # 3. Later bars without sell signal (bar 40): held long
    w_held = compute_weights({"QQQ": df.iloc[:40]}, DEFAULT_PERIOD, DEFAULT_MULT)
    assert w_held == {"QQQ": pytest.approx(0.2)}

    # 4. On sell signal (bar 53): flat
    w_sell = compute_weights({"QQQ": df.iloc[:53]}, DEFAULT_PERIOD, DEFAULT_MULT)
    assert w_sell == {}

    # 5. Subsequent bars after sell (bar 58): still flat
    w_flat = compute_weights({"QQQ": df.iloc[:58]}, DEFAULT_PERIOD, DEFAULT_MULT)
    assert w_flat == {}


def test_two_runs_on_identical_data_give_identical_weights():
    """Test 2: Two runs on identical data give identical weights (stateless idempotence)."""
    prices = np.concatenate(
        [
            np.linspace(100.0, 80.0, 25),
            np.linspace(80.0, 120.0, 20),
        ]
    )
    frames = {
        "QQQ": _make_ohlcv(prices),
        "SPY": _make_ohlcv(prices * 1.05),
        "TLT": _make_ohlcv(np.linspace(100.0, 90.0, 45)),
    }

    run1 = compute_weights(frames, DEFAULT_PERIOD, DEFAULT_MULT)
    run2 = compute_weights(frames, DEFAULT_PERIOD, DEFAULT_MULT)

    assert run1 == run2
    assert set(run1.keys()) == {"QQQ", "SPY"}
    for ticker, weight in run1.items():
        assert weight == pytest.approx(0.2)


def test_stale_entry_not_invented_when_no_buy_in_window():
    """Test 3: A stale entry older than the loaded window is not invented."""
    prices = np.linspace(120.0, 80.0, 50)
    df = _make_ohlcv(prices)

    weights = compute_weights({"QQQ": df}, DEFAULT_PERIOD, DEFAULT_MULT)
    assert weights == {}


def test_warmup_guard_returns_empty():
    """Test 4: Fewer rows than warmup returns empty dictionary."""
    short_df = _make_ohlcv(np.linspace(100.0, 110.0, 15))
    assert compute_weights({"QQQ": short_df}, period=14) == {}
    assert compute_weights({}, period=14) == {}


def test_weights_security_and_bounds():
    """Test 5: Weights are non-negative, capped, and respect universe bounds."""
    prices_up = np.concatenate([np.linspace(100.0, 80.0, 25), np.linspace(80.0, 120.0, 20)])
    prices_down = np.linspace(120.0, 70.0, 45)

    frames = {
        "QQQ": _make_ohlcv(prices_up),
        "SPY": _make_ohlcv(prices_up),
        "IWM": _make_ohlcv(prices_up),
        "GLD": _make_ohlcv(prices_down),
        "TLT": _make_ohlcv(prices_down),
    }

    weights = compute_weights(frames, DEFAULT_PERIOD, DEFAULT_MULT)
    assert all(w >= 0.0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert set(weights.keys()).issubset(set(UNIVERSE))
    for ticker, weight in weights.items():
        assert weight == pytest.approx(0.2)


def test_bot_metadata_and_initialization(monkeypatch):
    """Test 6: Bot metadata, BACKTEST_PERIOD, and constructor parameters."""
    assert AlphaTrendBot.BACKTEST_PERIOD == "730d"
    assert AlphaTrendBot.param_grid == {
        "period": [10, 14, 20],
        "mult": [0.5, 1.0, 1.5, 2.0],
    }

    captured: dict = {}

    def fake_init(self, name, **kwargs):
        captured["name"] = name
        captured.update(kwargs)

    monkeypatch.setattr(Bot, "__init__", fake_init)
    AlphaTrendBot()

    assert captured["name"] == "AlphaTrendBot"
    assert captured["tickers"] == list(UNIVERSE)
    assert captured["interval"] == "1h"
    assert captured["period"] == "60d"
