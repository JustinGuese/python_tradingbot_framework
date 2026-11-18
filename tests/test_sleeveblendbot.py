"""
Tests for SleeveBlendBot (tradingbot/sleeveblendbot.py).

Step B1 of PLAN-alphaideas.md:
  1. Sleeve weights sum to 1.0.
  2. Composed weights sum <= 1.0.
  3. No events -> only price sleeves are invested and SHV weight equals event sleeves' share (0.205 + 0.036 = 0.241).
  4. A price sleeve's book is identical on two different days inside the same quarter.
"""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_continuous_futures_trend import UNIVERSE as CFT_UNIVERSE
from tradingbot.quantsmd_quarterly_te_cost_gate import UNIVERSE as QTE_UNIVERSE
from tradingbot.quantsmd_sma_daily_gated import UNIVERSE as SMA_UNIVERSE
from tradingbot.sleeveblendbot import (
    SLEEVE_WEIGHTS,
    UNIVERSE,
    SleeveBlendBot,
    compose_weights,
    last_month_first_bar,
    last_quarter_first_bar,
)
from tradingbot.utils.botclass import Bot


@pytest.fixture(autouse=True)
def stub_bot_init(monkeypatch):
    """Bypass Bot.__init__ database connection in tests."""

    def _fake_init(self, name, *args, **kwargs):
        self.bot_name = name
        self.tickers = list(kwargs.get("tickers") or UNIVERSE)
        self.benchmark_tickers = ()
        self.datas = {}

    monkeypatch.setattr(Bot, "__init__", _fake_init)


def _build_test_frame(end: str = "2024-03-25", periods: int = 600, seed: int = 42) -> pd.DataFrame:
    index = pd.bdate_range(end=end, periods=periods)
    rng = np.random.default_rng(seed)
    tickers = list(dict.fromkeys([*CFT_UNIVERSE, *SMA_UNIVERSE, *QTE_UNIVERSE, "SPY", "SHV"]))
    data = {}
    for t in tickers:
        drift = 0.001 if t != "SHV" else 0.0001
        noise = rng.normal(0.0, 0.005, size=periods)
        data[t] = 100.0 * np.exp(np.cumsum(drift + noise))
    return pd.DataFrame(data, index=index)


# =========================================================================== #
# Acceptance Test 1: Sleeve weights sum to 1.0
# =========================================================================== #


def test_sleeve_weights_sum_to_one():
    """Sleeve weights must sum to 1.0 and match published H1 weights."""
    assert sum(SLEEVE_WEIGHTS.values()) == pytest.approx(1.0)
    assert SLEEVE_WEIGHTS["continuous_futures_trend"] == pytest.approx(0.394)
    assert SLEEVE_WEIGHTS["buyback_sp100"] == pytest.approx(0.205)
    assert SLEEVE_WEIGHTS["sma_daily_gated"] == pytest.approx(0.185)
    assert SLEEVE_WEIGHTS["quarterly_te_cost_gate"] == pytest.approx(0.180)
    assert SLEEVE_WEIGHTS["activist_13d"] == pytest.approx(0.036)


# =========================================================================== #
# Acceptance Test 2: Composed weights sum <= 1.0
# =========================================================================== #


def test_composed_weights_sum_leq_one():
    """Composed blend weights must be non-negative and sum to <= 1.0."""
    closes = _build_test_frame("2024-02-14")
    # Provide sample active events
    buyback_events = [
        {"symbol": "AAPL", "acceptance_datetime": "2024-02-10T12:00:00Z"},
    ]
    sc13d_events = [
        {"symbol": "MSFT", "filed_at": "2024-02-05T12:00:00Z", "group_type": "fund"},
    ]

    weights = compose_weights(
        closes,
        day=date(2024, 2, 14),
        buyback_events=buyback_events,
        sc13d_events=sc13d_events,
    )

    assert isinstance(weights, dict)
    assert len(weights) > 0
    assert all(w >= 0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9


# =========================================================================== #
# Acceptance Test 3: No events -> only price sleeves invested, SHV = event share
# =========================================================================== #


def test_no_events_only_price_sleeves_invested_shv_equals_event_share():
    """With no events, only price sleeves are invested and SHV weight equals event sleeves' share (0.241)."""
    # Create closes where all price sleeves are fully invested (100% gross each):
    # - CFT: strong positive drift -> all 6 ETFs uptrending -> 1.0 * 0.394
    # - QTE: all ETF8 uptrending -> top 3 equal-weighted -> 1.0 * 0.180
    # - SMA: strong uptrend across all sectors and SPY -> 4 sectors @ 0.25 = 1.0 * 0.185
    periods = 600
    index = pd.bdate_range(end="2024-02-14", periods=periods)
    tickers = list(dict.fromkeys([*CFT_UNIVERSE, *SMA_UNIVERSE, *QTE_UNIVERSE, "SPY", "SHV"]))
    data = {}
    for t in tickers:
        # Strong steady positive drift guarantees all moving averages are in uptrends
        drift = 0.005 if t != "SHV" else 0.0001
        data[t] = 100.0 * np.exp(drift * np.arange(periods))
    closes = pd.DataFrame(data, index=index)

    # Empty event lists: no active events
    weights = compose_weights(
        closes,
        day=date(2024, 2, 14),
        buyback_events=[],
        sc13d_events=[],
    )

    # Event sleeves' share: buyback (0.205) + activist (0.036) = 0.241
    expected_event_share = SLEEVE_WEIGHTS["buyback_sp100"] + SLEEVE_WEIGHTS["activist_13d"]
    assert expected_event_share == pytest.approx(0.241)

    assert "SHV" in weights
    assert weights["SHV"] == pytest.approx(expected_event_share, abs=1e-4)

    # Non-SHV weights must be exclusively price sleeve assets
    price_sleeve_symbols = set(CFT_UNIVERSE) | set(SMA_UNIVERSE) | set(QTE_UNIVERSE)
    non_shv_symbols = set(weights.keys()) - {"SHV"}
    assert non_shv_symbols.issubset(price_sleeve_symbols)

    # Total weights sum to 1.0 (price sleeves 0.759 + SHV 0.241 = 1.000)
    assert sum(weights.values()) == pytest.approx(1.0, abs=1e-4)


# =========================================================================== #
# Acceptance Test 4: A price sleeve's book is identical on two different days inside the same quarter
# =========================================================================== #


def test_price_sleeve_book_identical_on_two_different_days_inside_same_quarter():
    """A price sleeve's book is identical on two different days inside the same quarter."""
    closes = _build_test_frame("2024-03-25")

    # Day 1: mid-quarter (Feb 14, 2024)
    day1_ts = "2024-02-14"
    closes1 = closes.loc[:day1_ts]

    # Day 2: late-quarter (March 20, 2024)
    day2_ts = "2024-03-20"
    closes2 = closes.loc[:day2_ts]

    # QuarterlyTeCostGate truncates to the first bar of Q1 2024
    q_bar1 = last_quarter_first_bar(closes1.index)
    q_bar2 = last_quarter_first_bar(closes2.index)
    assert q_bar1 == q_bar2  # Both truncate to 2024-01-02

    # Verify composed weights for the quarterly sleeve alone are exactly identical
    qte_only_weights = {"quarterly_te_cost_gate": 1.0}
    w1 = compose_weights(closes1, sleeve_weights=qte_only_weights, buyback_events=[], sc13d_events=[])
    w2 = compose_weights(closes2, sleeve_weights=qte_only_weights, buyback_events=[], sc13d_events=[])
    assert w1 == w2

    # ContinuousFuturesTrend truncates to the first bar of the current month
    # Verify CFT book is identical on two days inside the same month (e.g. Feb 14 and Feb 28)
    closes_feb14 = closes.loc[:"2024-02-14"]
    closes_feb28 = closes.loc[:"2024-02-28"]
    m_bar1 = last_month_first_bar(closes_feb14.index)
    m_bar2 = last_month_first_bar(closes_feb28.index)
    assert m_bar1 == m_bar2  # Both truncate to 2024-02-01

    cft_only_weights = {"continuous_futures_trend": 1.0}
    w_cft1 = compose_weights(closes_feb14, sleeve_weights=cft_only_weights, buyback_events=[], sc13d_events=[])
    w_cft2 = compose_weights(closes_feb28, sleeve_weights=cft_only_weights, buyback_events=[], sc13d_events=[])
    assert w_cft1 == w_cft2


# =========================================================================== #
# Bot class metadata and targetWeights integration
# =========================================================================== #


def test_sleeveblendbot_metadata_and_target_weights():
    """Verify SleeveBlendBot attributes and targetWeights integration."""
    closes = _build_test_frame("2024-02-14")
    bot = SleeveBlendBot(buyback_events=[], sc13d_events=[])
    assert "SHV" in bot.tickers
    assert bot.MIN_UNIVERSE_COVERAGE == 0.90
    assert bot.BACKTEST_PERIOD == "max"

    bot.datas = {t: pd.DataFrame({"close": closes[t]}, index=closes.index) for t in closes.columns}
    weights = bot.targetWeights({})
    assert isinstance(weights, dict)
    assert len(weights) > 0
    assert "SHV" in weights
    assert sum(weights.values()) == pytest.approx(1.0)
