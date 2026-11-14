"""
Tests for BuybackBroadBot (PLAN.md step S3a).

Key invariants tested:
1. NO SHORTS: long-only/flat, all weights >= 0.0.
2. Exposure caps: weight per symbol <= max_weight (default 0.5), sum <= 1.0.
3. Idle cash: returns {} when no events are active.
4. Broad universe support: handles S&P 400 and S&P 600 names.
5. Holding duration: positions age out after hold_sessions.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from tradingbot.buybackbroadbot import BuybackBroadBot
from tradingbot.utils.botclass import Bot

UTC = ZoneInfo("UTC")


@pytest.fixture
def make_bot(mocker):
    """Create a BuybackBroadBot with Bot.__init__ network/DB stubbed out."""
    mocker.patch.object(Bot, "__init__", lambda self, *args, **kwargs: None)

    def _factory(events=None, max_weight=0.5, hold_sessions=10, tickers=None):
        bot = BuybackBroadBot(
            events=events,
            tickers=tickers,
            max_weight=max_weight,
            hold_sessions=hold_sessions,
        )
        bot.bot_name = "BuybackBroadBot"
        bot.interval = "1d"
        bot.period = "1mo"
        return bot

    return _factory


def _make_rows(day: date) -> dict[str, pd.Series]:
    return {"QQQ": pd.Series({"timestamp": pd.Timestamp(day)})}


def test_no_shorts_and_weights_non_negative(make_bot):
    """Weights must strictly be >= 0 under all circumstances (no shorts allowed)."""
    events = [
        {"symbol": "DECK", "entry_session": date(2024, 1, 16)},
        {"symbol": "TREX", "entry_session": date(2024, 1, 16)},
    ]
    bot = make_bot(events=events)
    weights = bot.targetWeights(_make_rows(date(2024, 1, 16)))

    assert len(weights) == 2
    for sym, w in weights.items():
        assert w >= 0.0, f"Short or negative weight found for {sym}: {w}"


def test_exposure_cap_single_active_event(make_bot):
    """A single active event must be capped at max_weight (default 0.5), not 1.0."""
    events = [{"symbol": "MTD", "entry_session": date(2024, 1, 16)}]
    bot = make_bot(events=events, max_weight=0.5)
    weights = bot.targetWeights(_make_rows(date(2024, 1, 16)))

    assert weights == {"MTD": 0.5}

    # Custom cap
    bot_custom = make_bot(events=events, max_weight=0.25)
    weights_custom = bot_custom.targetWeights(_make_rows(date(2024, 1, 16)))
    assert weights_custom == {"MTD": 0.25}


def test_exposure_caps_multiple_events(make_bot):
    """When multiple events are active, each receives min(1/n, cap) and sum <= 1.0."""
    bar_day = date(2024, 1, 16)
    symbols = ["AAPL", "LFUS", "HELE", "WSO", "FIX"]
    events = [{"symbol": s, "entry_session": bar_day} for s in symbols]

    bot = make_bot(events=events, max_weight=0.5)
    weights = bot.targetWeights(_make_rows(bar_day))

    assert len(weights) == 5
    for s in symbols:
        assert pytest.approx(weights[s]) == 0.20
    assert sum(weights.values()) <= 1.0 + 1e-6


def test_no_active_events_returns_empty_dict(make_bot):
    """When no events are active, targetWeights returns {} (100% idle cash)."""
    bot = make_bot(events=[])
    weights = bot.targetWeights(_make_rows(date(2024, 1, 16)))
    assert weights == {}


def test_event_aging_out_after_hold_sessions(make_bot):
    """An event held beyond hold_sessions (10 NYSE sessions) must drop out."""
    # 2024-01-16 is Tuesday. 10 sessions ends on 2024-01-30.
    entry = date(2024, 1, 16)
    event = {"symbol": "DECK", "entry_session": entry}
    bot = make_bot(events=[event], hold_sessions=10)

    # Active on session 1
    w_day1 = bot.targetWeights(_make_rows(entry))
    assert "DECK" in w_day1

    # Active on session 9 (2024-01-29)
    w_day9 = bot.targetWeights(_make_rows(date(2024, 1, 29)))
    assert "DECK" in w_day9

    # Inactive on session 11 (2024-01-31)
    w_day11 = bot.targetWeights(_make_rows(date(2024, 1, 31)))
    assert w_day11 == {}


def test_broad_universe_sp400_sp600_symbols(make_bot):
    """Verifies that mid/small-cap symbols from S&P 400 and S&P 600 are correctly handled."""
    events = [
        {"symbol": "MSFT", "entry_session": date(2024, 1, 16)},  # SP100
        {"symbol": "EXP", "entry_session": date(2024, 1, 16)},   # SP400
        {"symbol": "CALM", "entry_session": date(2024, 1, 16)},  # SP600
    ]
    bot = make_bot(events=events, max_weight=0.5)
    weights = bot.targetWeights(_make_rows(date(2024, 1, 16)))

    assert set(weights.keys()) == {"MSFT", "EXP", "CALM"}
    for w in weights.values():
        assert pytest.approx(w) == 1.0 / 3.0
        assert w >= 0.0
