"""
Tests for BuybackDriftBot (Step P2).

Tests:
1. no events -> {}
2. one active event -> {sym: 0.5} (deviation from study: capped at 0.5)
3. three actives -> 1/3 each
4. event older than 10 sessions drops out
5. stale scanner raises (empty table or MAX(created_at) older than 10 days)
"""

from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock

import pandas as pd
import pytest

from tradingbot.buybackdriftbot import BuybackDriftBot, read_buyback_events
from tradingbot.utils.botclass import Bot
from tradingbot.utils.market_calendar import next_session
from tradingbot.utils.universes import SP100


@pytest.fixture
def make_bot(mocker):
    """Create a BuybackDriftBot with DB initialization stubbed out."""
    mocker.patch.object(Bot, "__init__", lambda self, *args, **kwargs: None)

    def _factory(events=None):
        bot = BuybackDriftBot(events=events)
        bot.bot_name = "BuybackDriftBot"
        bot.tickers = list(SP100)
        bot.interval = "1d"
        bot.period = "1mo"
        bot._events = events
        return bot

    return _factory


def _make_rows(day: date) -> dict[str, pd.Series]:
    """Create minimal rows dict passed by framework to targetWeights."""
    return {"AAPL": pd.Series({"timestamp": pd.Timestamp(day)})}


# ------------------------------------------------------------------
# Bot configuration & contracts
# ------------------------------------------------------------------


def test_bot_attributes(make_bot):
    bot = make_bot()
    assert bot.bot_name == "BuybackDriftBot"
    assert bot.interval == "1d"
    assert bot.period == "1mo"
    assert bot.MIN_UNIVERSE_COVERAGE == 0.95
    assert set(bot.tickers) == set(SP100)


# ------------------------------------------------------------------
# targetWeights logic
# ------------------------------------------------------------------


def test_no_events_returns_empty_dict(make_bot, monkeypatch):
    """When no events are returned, targetWeights returns {} (holds 100% cash)."""
    monkeypatch.setattr("tradingbot.buybackdriftbot.read_buyback_events", lambda: [])
    bot = make_bot()
    rows = _make_rows(date(2024, 1, 15))

    weights = bot.targetWeights(rows)
    assert weights == {}


def test_one_active_event_capped_at_half(make_bot, monkeypatch):
    """
    One active event allocates 0.5 to that symbol (capped at 0.5, remaining 0.5 in cash).
    """
    event = {
        "symbol": "AAPL",
        "entry_session": date(2024, 1, 16),
        "filed_at": datetime(2024, 1, 15, 22, 0, tzinfo=UTC),
    }
    monkeypatch.setattr("tradingbot.buybackdriftbot.read_buyback_events", lambda: [event])
    bot = make_bot()
    rows = _make_rows(date(2024, 1, 16))

    weights = bot.targetWeights(rows)
    assert weights == {"AAPL": 0.5}


def test_three_actives_equal_weight(make_bot, monkeypatch):
    """Three active events receive equal 1/3 weight each."""
    bar_day = date(2024, 1, 16)
    events = [
        {"symbol": "AAPL", "entry_session": bar_day},
        {"symbol": "MSFT", "entry_session": bar_day},
        {"symbol": "NVDA", "entry_session": bar_day},
    ]
    monkeypatch.setattr("tradingbot.buybackdriftbot.read_buyback_events", lambda: events)
    bot = make_bot()
    rows = _make_rows(bar_day)

    weights = bot.targetWeights(rows)
    assert len(weights) == 3
    for sym in ["AAPL", "MSFT", "NVDA"]:
        assert weights[sym] == pytest.approx(1.0 / 3.0)


def test_event_older_than_ten_sessions_drops_out(make_bot, monkeypatch):
    """An event held for 10 sessions drops out on the 11th session (returns {})."""
    # 2024-01-16 is Tuesday. 10 sessions later:
    curr = date(2024, 1, 16)
    for _ in range(10):
        curr = next_session(curr)
    day_11 = curr  # 10 sessions have passed, day_11 is session index 10 (exited)

    event = {
        "symbol": "AAPL",
        "entry_session": date(2024, 1, 16),
    }
    monkeypatch.setattr("tradingbot.buybackdriftbot.read_buyback_events", lambda: [event])
    bot = make_bot()
    rows = _make_rows(day_11)

    weights = bot.targetWeights(rows)
    assert weights == {}


# ------------------------------------------------------------------
# Stale scanner / empty table detection
# ------------------------------------------------------------------


def test_stale_scanner_empty_table_raises(monkeypatch):
    """Empty buyback_events table raises RuntimeError so dead scanner fails loudly."""
    mock_session = MagicMock()
    mock_query = MagicMock()
    mock_query.scalar.return_value = None  # MAX(created_at) is None
    mock_session.query.return_value = mock_query

    @contextmanager
    def _mock_get_db():
        yield mock_session

    monkeypatch.setattr("tradingbot.buybackdriftbot.get_db_session", _mock_get_db)

    with pytest.raises(RuntimeError, match="buyback_events table is empty; scanner has never run"):
        read_buyback_events()


def test_stale_scanner_old_created_at_raises(monkeypatch):
    """MAX(created_at) older than 10 days raises RuntimeError."""
    now = datetime(2024, 2, 1, 12, 0, tzinfo=UTC)
    stale_created = now - timedelta(days=12)

    mock_session = MagicMock()
    mock_query = MagicMock()
    mock_query.scalar.return_value = stale_created.replace(tzinfo=None)
    mock_session.query.return_value = mock_query

    @contextmanager
    def _mock_get_db():
        yield mock_session

    monkeypatch.setattr("tradingbot.buybackdriftbot.get_db_session", _mock_get_db)

    with pytest.raises(RuntimeError, match="buyback scanner is stale"):
        read_buyback_events(now=now)


def test_read_buyback_events_returns_rows_within_cutoff(monkeypatch):
    """When scanner is fresh, read_buyback_events queries and returns events."""
    now = datetime(2024, 2, 1, 12, 0, tzinfo=UTC)
    fresh_created = now - timedelta(days=2)

    class DummyRow:
        id = 10
        symbol = "AAPL"
        accession = "0000320193-24-000001"
        filed_at = datetime(2024, 1, 20, 21, 5)
        source_url = "https://sec.gov"
        snippet = "board authorized repurchase"
        created_at = fresh_created.replace(tzinfo=None)

    mock_session = MagicMock()
    mock_query_max = MagicMock()
    mock_query_max.scalar.return_value = fresh_created.replace(tzinfo=None)

    mock_query_rows = MagicMock()
    mock_query_rows.filter.return_value.order_by.return_value.all.return_value = [DummyRow()]

    def _query(entity):
        # If querying func.max(created_at)
        if hasattr(entity, "element") or "max" in str(entity).lower():
            return mock_query_max
        return mock_query_rows

    mock_session.query.side_effect = _query

    @contextmanager
    def _mock_get_db():
        yield mock_session

    monkeypatch.setattr("tradingbot.buybackdriftbot.get_db_session", _mock_get_db)

    events = read_buyback_events(since_days=40, now=now)
    assert len(events) == 1
    assert events[0]["symbol"] == "AAPL"
    assert events[0]["accession"] == "0000320193-24-000001"


def test_target_weights_propagates_stale_scanner_error(make_bot, monkeypatch):
    """targetWeights does not suppress a dead scanner error."""
    def _raise():
        raise RuntimeError("buyback scanner is stale")

    monkeypatch.setattr("tradingbot.buybackdriftbot.read_buyback_events", _raise)
    bot = make_bot()
    rows = _make_rows(date(2024, 1, 16))

    with pytest.raises(RuntimeError, match="buyback scanner is stale"):
        bot.targetWeights(rows)
