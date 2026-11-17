"""
Tests for BuybackHedgedBot (Step S2c).

Contracts tested:
- target weights sum to 1 signed (signed sum of stock weights + cash = 1.0);
- QQQ weight negative;
- gross <= 2.0;
- flat when no signals (no stranded short).
"""

from datetime import UTC, date, datetime

import pandas as pd
import pytest

from tradingbot.buybackhedgedbot import BuybackHedgedBot
from tradingbot.utils.botclass import Bot
from tradingbot.utils.universes import SP100
from tradingbot.utils.weights import gross_weight


@pytest.fixture
def make_bot(mocker):
    """Create a BuybackHedgedBot with DB initialization stubbed out."""
    mocker.patch.object(Bot, "__init__", lambda self, *args, **kwargs: None)

    def _factory(events=None):
        bot = BuybackHedgedBot(events=events)
        bot.bot_name = "BuybackHedgedBot"
        bot.tickers = list(dict.fromkeys((*SP100, "QQQ")))
        bot.interval = "1d"
        bot.period = "1mo"
        bot._events = events
        return bot

    return _factory


def _make_rows(day: date) -> dict[str, pd.Series]:
    """Create minimal rows dict passed by framework to targetWeights."""
    return {"AAPL": pd.Series({"timestamp": pd.Timestamp(day)})}


# ------------------------------------------------------------------
# 1. Bot configuration & attributes
# ------------------------------------------------------------------


def test_bot_attributes(make_bot):
    bot = make_bot()
    assert bot.bot_name == "BuybackHedgedBot"
    assert bot.ALLOW_SHORT is True
    assert bot.MAX_GROSS == 2.0
    assert bot.SHORTABLE == ("QQQ",)
    assert "QQQ" in bot.SHORTABLE
    assert "QQQ" in bot.tickers
    assert set(SP100).issubset(set(bot.tickers))
    assert bot.interval == "1d"
    assert bot.period == "1mo"
    assert bot.MIN_UNIVERSE_COVERAGE == 0.95


# ------------------------------------------------------------------
# 2. No signals -> flat (no stranded short)
# ------------------------------------------------------------------


def test_no_signals_returns_empty_and_flat(make_bot, monkeypatch):
    """When no buyback events are active, return {} and normalise to 100% cash."""
    monkeypatch.setattr("tradingbot.buybackdriftbot.read_buyback_events", lambda: [])
    bot = make_bot()
    rows = _make_rows(date(2024, 1, 15))

    raw = bot.targetWeights(rows)
    assert raw == {}
    assert "QQQ" not in raw

    coerced = bot._coerce_target_weights(raw, set(bot.tickers))
    assert coerced == {"USD": 1.0}
    assert "QQQ" not in coerced
    assert sum(coerced.values()) == pytest.approx(1.0)


def test_empty_rows_returns_empty(make_bot):
    bot = make_bot()
    assert bot.targetWeights({}) == {}


# ------------------------------------------------------------------
# 3. Active signals -> hedged with QQQ (negative weight, sum to 1, gross <= 2.0)
# ------------------------------------------------------------------


def test_single_active_signal_hedged(make_bot, monkeypatch):
    """
    One active buyback event: 0.5 long capped, hedged by -0.5 QQQ short.
    Signed sum of stock weights + cash = 1.0, gross <= 2.0.
    """
    event = {
        "symbol": "AAPL",
        "entry_session": date(2024, 1, 16),
        "filed_at": datetime(2024, 1, 15, 22, 0, tzinfo=UTC),
    }
    monkeypatch.setattr("tradingbot.buybackdriftbot.read_buyback_events", lambda: [event])
    bot = make_bot()
    rows = _make_rows(date(2024, 1, 16))

    raw = bot.targetWeights(rows)
    assert raw == {"AAPL": 0.5, "QQQ": -0.5}
    assert raw["QQQ"] < 0  # QQQ weight negative

    coerced = bot._coerce_target_weights(raw, set(bot.tickers))
    assert coerced["AAPL"] == pytest.approx(0.5)
    assert coerced["QQQ"] == pytest.approx(-0.5)
    assert coerced["USD"] == pytest.approx(1.0)
    assert sum(coerced.values()) == pytest.approx(1.0)  # signed sum = 1.0
    assert gross_weight(coerced) == pytest.approx(1.0)
    assert gross_weight(coerced) <= 2.0  # gross <= 2.0


def test_multiple_active_signals_hedged_gross_le_two(make_bot, monkeypatch):
    """
    Multiple active buyback events: long weights hedged by -sum(long_weights) on QQQ.
    Signed sum = 1.0, QQQ negative, gross <= 2.0.
    """
    bar_day = date(2024, 1, 16)
    events = [
        {"symbol": "AAPL", "entry_session": bar_day},
        {"symbol": "MSFT", "entry_session": bar_day},
        {"symbol": "NVDA", "entry_session": bar_day},
    ]
    monkeypatch.setattr("tradingbot.buybackdriftbot.read_buyback_events", lambda: events)
    bot = make_bot()
    rows = _make_rows(bar_day)

    raw = bot.targetWeights(rows)
    assert len(raw) == 4
    for sym in ["AAPL", "MSFT", "NVDA"]:
        assert raw[sym] == pytest.approx(1.0 / 3.0)
    assert raw["QQQ"] == pytest.approx(-1.0)  # QQQ weight negative
    assert raw["QQQ"] < 0

    coerced = bot._coerce_target_weights(raw, set(bot.tickers))
    assert sum(coerced.values()) == pytest.approx(1.0)  # signed sum = 1.0
    assert gross_weight(coerced) == pytest.approx(2.0)
    assert gross_weight(coerced) <= 2.0  # gross <= 2.0
