"""Unit tests for TvTrendDailyBot (Step S2 of PLAN-tvdaily.md).

Tests:
- compute_weights purity (idempotent, no side effects, deterministic).
- compute_weights empty input, missing rows guard.
- compute_weights per-ticker cap (0.2), gross_cap, vote thresholds, no USD key.
- TvTrendDailyBot class properties, initialization, and targetWeights.
"""

from __future__ import annotations

import copy
from typing import Any

import numpy as np
import pandas as pd
import pytest

from tradingbot.tvtrenddailybot import (
    DEFAULT_GROSS_CAP,
    DEFAULT_MIN_ROWS,
    DEFAULT_MIN_VOTES,
    WINNING_MIN_HOLD,
    WINNING_SIGNAL_PARAMS,
    TvTrendDailyBot,
    compute_weights,
)
from tradingbot.utils.tv_signals import UNIVERSE_WIDE


@pytest.fixture
def sample_frames() -> dict[str, pd.DataFrame]:
    """Generate 100-bar sample OHLCV data for multiple tickers."""
    np.random.seed(42)
    dates = pd.date_range("2020-01-01", periods=100, freq="D")
    frames = {}

    # SPY: strong uptrend (should qualify for all bullish signals)
    close_spy = np.linspace(100, 200, 100) + np.random.randn(100) * 0.5
    frames["SPY"] = pd.DataFrame(
        {
            "open": close_spy - 0.5,
            "high": close_spy + 1.0,
            "low": close_spy - 1.0,
            "close": close_spy,
            "volume": 1000.0,
        },
        index=dates,
    )

    # QQQ: strong uptrend (should qualify for all bullish signals)
    close_qqq = np.linspace(150, 280, 100) + np.random.randn(100) * 0.5
    frames["QQQ"] = pd.DataFrame(
        {
            "open": close_qqq - 0.5,
            "high": close_qqq + 1.0,
            "low": close_qqq - 1.0,
            "close": close_qqq,
            "volume": 1000.0,
        },
        index=dates,
    )

    # TLT: strong downtrend (should fail all bullish signals)
    close_tlt = np.linspace(150, 80, 100) + np.random.randn(100) * 0.5
    frames["TLT"] = pd.DataFrame(
        {
            "open": close_tlt + 0.5,
            "high": close_tlt + 1.0,
            "low": close_tlt - 1.0,
            "close": close_tlt,
            "volume": 1000.0,
        },
        index=dates,
    )

    return frames


def test_compute_weights_purity(sample_frames: dict[str, pd.DataFrame]) -> None:
    """Verify compute_weights is pure: calling it twice with identical input returns identical output without mutation."""
    frames_copy = copy.deepcopy(sample_frames)
    w1 = compute_weights(sample_frames, min_votes=2)
    w2 = compute_weights(sample_frames, min_votes=2)

    assert w1 == w2
    # Verify input frames were not mutated
    for k in sample_frames:
        pd.testing.assert_frame_equal(sample_frames[k], frames_copy[k])


def test_compute_weights_empty_and_warmup(sample_frames: dict[str, pd.DataFrame]) -> None:
    assert compute_weights({}) == {}
    assert compute_weights({"SPY": pd.DataFrame()}) == {}

    # Frame with fewer than min_rows (e.g. 30 rows < 60)
    short_frames = {k: v.iloc[:30] for k, v in sample_frames.items()}
    assert compute_weights(short_frames, min_rows=60) == {}


def test_compute_weights_caps_and_no_usd(sample_frames: dict[str, pd.DataFrame]) -> None:
    # SPY and QQQ qualify; TLT downtrend fails
    w = compute_weights(sample_frames, min_votes=2, gross_cap=1.0)
    assert "TLT" not in w
    assert "USD" not in w
    assert "SPY" in w
    assert "QQQ" in w

    # Per-ticker cap of 0.20
    assert w["SPY"] <= 0.200001
    assert w["QQQ"] <= 0.200001
    assert sum(w.values()) <= 1.000001


def test_compute_weights_gross_cap(sample_frames: dict[str, pd.DataFrame]) -> None:
    # Capped by gross_cap=0.30 -> each gets min(0.2, 0.3 / 2) = 0.15
    w = compute_weights(sample_frames, min_votes=2, gross_cap=0.30)
    assert w["SPY"] == pytest.approx(0.15)
    assert w["QQQ"] == pytest.approx(0.15)
    assert sum(w.values()) <= 0.300001


def test_compute_weights_vote_threshold(sample_frames: dict[str, pd.DataFrame]) -> None:
    # If min_votes is set higher than any votes received, returns empty dict
    w_impossible = compute_weights(sample_frames, min_votes=5)
    assert w_impossible == {}


def test_tvtrenddailybot_instantiation(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_init(self, name: str, **kwargs: Any) -> None:
        captured["name"] = name
        captured.update(kwargs)
        self.tickers = kwargs.get("tickers", [])
        self.benchmark_tickers = []
        self.datas = {}

    from tradingbot.utils.botclass import Bot

    monkeypatch.setattr(Bot, "__init__", fake_init)

    bot = TvTrendDailyBot()
    assert captured["name"] == "TvTrendDailyBot"
    assert captured["interval"] == "1d"
    assert captured["tickers"] == list(UNIVERSE_WIDE)
    assert bot.min_votes == DEFAULT_MIN_VOTES
    assert bot.gross_cap == DEFAULT_GROSS_CAP
    assert bot.min_rows == DEFAULT_MIN_ROWS
    assert bot.signal_params == WINNING_SIGNAL_PARAMS
    assert bot.min_holds == WINNING_MIN_HOLD

    # Verify targetWeights returns empty dict when datas is empty
    assert bot.targetWeights({}) == {}


def test_single_signal_bots_use_only_their_kind(sample_frames):
    from tradingbot.supertrenddailybot import SupertrendDailyBot

    assert SupertrendDailyBot.KIND == "supertrend"
    w = compute_weights(sample_frames, min_votes=1, kinds=("supertrend",))
    assert "USD" not in w and all(0 < x <= 0.2 for x in w.values())


def test_compute_weights_defensive_rotation_active(sample_frames: dict[str, pd.DataFrame]) -> None:
    """When risk assets are partially allocated, active defensives are allocated up to gross_cap."""
    dates = sample_frames["SPY"].index
    close_gld = np.linspace(100, 180, len(dates))
    gld_frame = pd.DataFrame(
        {
            "open": close_gld - 0.5,
            "high": close_gld + 1.0,
            "low": close_gld - 1.0,
            "close": close_gld,
            "volume": 1000.0,
        },
        index=dates,
    )
    frames = dict(sample_frames)
    frames["GLD"] = gld_frame

    w = compute_weights(frames, min_votes=2, gross_cap=1.0, use_defensive=True)

    assert "USD" not in w
    assert "TLT" not in w
    assert "SPY" in w and w["SPY"] <= 0.200001
    assert "QQQ" in w and w["QQQ"] <= 0.200001
    assert "GLD" in w and w["GLD"] <= 0.200001
    assert sum(w.values()) <= 1.000001
    assert w["GLD"] == pytest.approx(0.20)


def test_compute_weights_defensive_rotation_full_risk(sample_frames: dict[str, pd.DataFrame]) -> None:
    """When risk assets use entire gross_cap, defensives receive 0 allocation."""
    dates = sample_frames["SPY"].index
    frames = {}
    for name in ["R1", "R2", "R3", "R4", "R5"]:
        c = np.linspace(100, 200, len(dates))
        frames[name] = pd.DataFrame(
            {"open": c - 0.5, "high": c + 1.0, "low": c - 1.0, "close": c, "volume": 1000.0},
            index=dates,
        )
    c_gld = np.linspace(100, 200, len(dates))
    frames["GLD"] = pd.DataFrame(
        {"open": c_gld - 0.5, "high": c_gld + 1.0, "low": c_gld - 1.0, "close": c_gld, "volume": 1000.0},
        index=dates,
    )

    w = compute_weights(
        frames,
        min_votes=2,
        gross_cap=1.0,
        use_defensive=True,
        defensive_assets=("GLD",),
    )

    assert "USD" not in w
    for name in ["R1", "R2", "R3", "R4", "R5"]:
        assert name in w and w[name] == pytest.approx(0.20)
    assert "GLD" not in w
    assert sum(w.values()) <= 1.000001


def test_compute_weights_defensive_rotation_disabled(sample_frames: dict[str, pd.DataFrame]) -> None:
    """When use_defensive=False, defensive assets are treated as normal voting assets."""
    dates = sample_frames["SPY"].index
    close_gld = np.linspace(100, 180, len(dates))
    frames = dict(sample_frames)
    frames["GLD"] = pd.DataFrame(
        {
            "open": close_gld - 0.5,
            "high": close_gld + 1.0,
            "low": close_gld - 1.0,
            "close": close_gld,
            "volume": 1000.0,
        },
        index=dates,
    )

    w_off = compute_weights(frames, min_votes=2, gross_cap=1.0, use_defensive=False)
    assert "USD" not in w_off
    assert set(w_off.keys()) == {"SPY", "QQQ", "GLD"}
    assert sum(w_off.values()) <= 1.000001
    for k in w_off:
        assert w_off[k] <= 0.200001


def test_ex_equity_bots_use_non_equity_universe():
    from tradingbot.alphatrendexequitybot import AlphaTrendExEquityBot
    from tradingbot.supertrendexequitybot import SupertrendExEquityBot
    from tradingbot.utils.tv_signals import UNIVERSE_EX_EQUITY

    assert SupertrendExEquityBot.UNIVERSE == UNIVERSE_EX_EQUITY
    assert AlphaTrendExEquityBot.UNIVERSE == UNIVERSE_EX_EQUITY
    assert not {"SPY", "QQQ", "IWM", "EFA", "EEM"} & set(UNIVERSE_EX_EQUITY)
    assert SupertrendExEquityBot.KIND == "supertrend"
    assert AlphaTrendExEquityBot.KIND == "alphatrend"
