"""Tests for BuybackSectorHedgedBot (Step D1).

Contracts tested:
- signed weights sum to 1.0 with USD;
- gross <= 2.0;
- every short is a sector ETF in SECTORS9;
- flat with no events (no stranded short).
"""

from datetime import UTC, date, datetime

import pandas as pd
import pytest

from tradingbot.buybacksectorhedgedbot import BuybackSectorHedgedBot
from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import SECTORS9
from tradingbot.utils.universes import SP100
from tradingbot.utils.weights import gross_weight


@pytest.fixture
def make_bot(mocker):
    """Create a BuybackSectorHedgedBot with DB initialization stubbed out."""
    mocker.patch.object(Bot, "__init__", lambda self, *args, **kwargs: None)

    def _factory(events=None, park_cash=True):
        bot = BuybackSectorHedgedBot(events=events, park_cash=park_cash)
        bot.bot_name = "BuybackSectorHedgedBot"
        bot.tickers = list(dict.fromkeys((*SP100, *SECTORS9, "SHV")))
        bot.interval = "1d"
        bot.period = "1mo"
        bot._events = events
        bot._park_cash = park_cash
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
    assert bot.bot_name == "BuybackSectorHedgedBot"
    assert bot.ALLOW_SHORT is True
    assert bot.MAX_GROSS == 2.0
    assert set(bot.SHORTABLE) == set(SECTORS9)
    assert bot.CASH_PROXY == "SHV"
    assert "SHV" in bot.tickers
    for sec in SECTORS9:
        assert sec in bot.SHORTABLE
        assert sec in bot.tickers
    assert set(SP100).issubset(set(bot.tickers))
    assert bot.interval == "1d"
    assert bot.period == "1mo"
    assert bot.MIN_UNIVERSE_COVERAGE == 0.95


# ------------------------------------------------------------------
# 2. No signals -> flat with no events (no stranded short)
# ------------------------------------------------------------------


def test_no_signals_returns_empty_and_flat(make_bot, monkeypatch):
    """When no buyback events are active, return {} and normalize to 100% cash."""
    monkeypatch.setattr("tradingbot.buybackdriftbot.read_buyback_events", lambda: [])
    bot = make_bot()
    rows = _make_rows(date(2024, 1, 15))

    raw = bot.targetWeights(rows)
    assert raw == {}
    for sec in SECTORS9:
        assert sec not in raw

    coerced = bot._coerce_target_weights(raw, set(bot.tickers))
    assert coerced == {"USD": 1.0}
    assert sum(coerced.values()) == pytest.approx(1.0)
    assert gross_weight(coerced) == pytest.approx(0.0)
    assert gross_weight(coerced) <= 2.0


def test_empty_rows_returns_empty(make_bot):
    bot = make_bot()
    assert bot.targetWeights({}) == {}


# ------------------------------------------------------------------
# 3. Active signals -> hedged with sector ETFs, signed sum = 1, gross <= 2.0
# ------------------------------------------------------------------


def test_single_active_signal_hedged_with_sector_etf(make_bot, monkeypatch):
    """One active event: long AAPL (0.5), hedged by short XLK (-0.5).

    Every short must be in SECTORS9, signed weights sum to 1.0 with USD, gross <= 2.0.
    """
    event = {
        "symbol": "AAPL",
        "entry_session": date(2024, 1, 16),
        "filed_at": datetime(2024, 1, 15, 22, 0, tzinfo=UTC),
    }
    monkeypatch.setattr("tradingbot.buybackdriftbot.read_buyback_events", lambda: [event])
    bot = make_bot(park_cash=True)
    rows = _make_rows(date(2024, 1, 16))

    raw = bot.targetWeights(rows)
    assert raw["AAPL"] == pytest.approx(0.5)
    assert raw["XLK"] == pytest.approx(-0.5)
    assert raw["SHV"] == pytest.approx(1.0)

    # Every short is a sector ETF in SECTORS9
    shorts = [sym for sym, w in raw.items() if w < 0]
    assert len(shorts) == 1
    assert shorts[0] == "XLK"
    assert shorts[0] in SECTORS9

    coerced = bot._coerce_target_weights(raw, set(bot.tickers))
    assert sum(coerced.values()) == pytest.approx(1.0)
    assert gross_weight(coerced) == pytest.approx(2.0)
    assert gross_weight(coerced) <= 2.0


def test_multiple_active_signals_same_sector(make_bot, monkeypatch):
    """Multiple active events in same sector (AAPL and MSFT -> XLK).

    XLK short is -1.0. Gross <= 2.0, signed sum = 1.0 with USD.
    """
    bar_day = date(2024, 1, 16)
    events = [
        {"symbol": "AAPL", "entry_session": bar_day},
        {"symbol": "MSFT", "entry_session": bar_day},
    ]
    monkeypatch.setattr("tradingbot.buybackdriftbot.read_buyback_events", lambda: events)
    bot = make_bot(park_cash=True)
    rows = _make_rows(bar_day)

    raw = bot.targetWeights(rows)
    assert raw["AAPL"] == pytest.approx(0.5)
    assert raw["MSFT"] == pytest.approx(0.5)
    assert raw["XLK"] == pytest.approx(-1.0)

    shorts = {sym: w for sym, w in raw.items() if w < 0}
    assert list(shorts.keys()) == ["XLK"]
    assert all(s in SECTORS9 for s in shorts)

    coerced = bot._coerce_target_weights(raw, set(bot.tickers))
    assert sum(coerced.values()) == pytest.approx(1.0)
    assert gross_weight(coerced) == pytest.approx(2.0)
    assert gross_weight(coerced) <= 2.0


def test_multiple_active_signals_different_sectors(make_bot, monkeypatch):
    """Multiple active events in different sectors:

    AAPL (XLK), JPM (XLF), XOM (XLE).
    Longs are 1/3 each, shorts are -1/3 each on XLK, XLF, XLE.
    """
    bar_day = date(2024, 1, 16)
    events = [
        {"symbol": "AAPL", "entry_session": bar_day},
        {"symbol": "JPM", "entry_session": bar_day},
        {"symbol": "XOM", "entry_session": bar_day},
    ]
    monkeypatch.setattr("tradingbot.buybackdriftbot.read_buyback_events", lambda: events)
    bot = make_bot()
    rows = _make_rows(bar_day)

    raw = bot.targetWeights(rows)
    for sym in ["AAPL", "JPM", "XOM"]:
        assert raw[sym] == pytest.approx(1.0 / 3.0)

    assert raw["XLK"] == pytest.approx(-1.0 / 3.0)
    assert raw["XLF"] == pytest.approx(-1.0 / 3.0)
    assert raw["XLE"] == pytest.approx(-1.0 / 3.0)

    shorts = {sym: w for sym, w in raw.items() if w < 0}
    assert set(shorts.keys()) == {"XLK", "XLF", "XLE"}
    assert all(s in SECTORS9 for s in shorts)

    coerced = bot._coerce_target_weights(raw, set(bot.tickers))
    assert sum(coerced.values()) == pytest.approx(1.0)
    assert gross_weight(coerced) == pytest.approx(2.0)
    assert gross_weight(coerced) <= 2.0


def test_without_park_cash(make_bot, monkeypatch):
    """With park_cash=False, uninvested portion remains as cash (USD).

    Signed weights sum to 1.0 with USD, gross <= 2.0, shorts in SECTORS9.
    """
    event = {
        "symbol": "AAPL",
        "entry_session": date(2024, 1, 16),
        "filed_at": datetime(2024, 1, 15, 22, 0, tzinfo=UTC),
    }
    monkeypatch.setattr("tradingbot.buybackdriftbot.read_buyback_events", lambda: [event])
    bot = make_bot(park_cash=False)
    rows = _make_rows(date(2024, 1, 16))

    raw = bot.targetWeights(rows)
    assert raw == {"AAPL": 0.5, "XLK": -0.5}
    assert "SHV" not in raw

    coerced = bot._coerce_target_weights(raw, set(bot.tickers))
    assert coerced["AAPL"] == pytest.approx(0.5)
    assert coerced["XLK"] == pytest.approx(-0.5)
    assert coerced["USD"] == pytest.approx(1.0)
    assert sum(coerced.values()) == pytest.approx(1.0)
    assert gross_weight(coerced) == pytest.approx(1.0)
    assert gross_weight(coerced) <= 2.0
