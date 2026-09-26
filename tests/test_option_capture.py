"""Daily option-chain capture (utils/option_capture.py) and the schema column sync it relies on."""

from datetime import date, timedelta
from typing import ClassVar

import pandas as pd
import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import StaticPool

from tradingbot.utils import option_capture, options
from tradingbot.utils.db import Base, OptionQuote, _sync_missing_columns

TODAY = date(2026, 9, 28)
SPOT = 100.0


class FakeTicker:
    market_state = "REGULAR"
    broken: ClassVar[set[str]] = set()

    def __init__(self, symbol):
        self.symbol = symbol
        if symbol in self.broken:
            raise ConnectionError("rate limited")
        self.options = tuple((TODAY + timedelta(days=d)).isoformat() for d in (3, 10, 17, 31, 59, 94, 185, 360, 725))

    def option_chain(self, expiry):
        exp = date.fromisoformat(expiry)

        def side(right):
            return pd.DataFrame(
                {
                    "contractSymbol": [f"{self.symbol}{exp:%y%m%d}{right}{k * 1000:08d}" for k in range(20, 205, 5)],
                    "strike": [float(k) for k in range(20, 205, 5)],
                    "lastPrice": 1.0,
                    "bid": 0.95,
                    "ask": 1.05,
                    "volume": 10.0,
                    "openInterest": 100.0,
                    "impliedVolatility": 0.3,
                }
            )

        chain = type("Chain", (), {})()
        chain.calls, chain.puts = side("C"), side("P")
        chain.underlying = {"regularMarketPrice": SPOT, "marketState": self.market_state}
        return chain


@pytest.fixture
def fake_yf(monkeypatch):
    FakeTicker.market_state = "REGULAR"
    FakeTicker.broken = set()
    monkeypatch.setattr(option_capture.yf, "Ticker", FakeTicker)
    monkeypatch.setattr(option_capture.time, "sleep", lambda _s: None)
    return FakeTicker


def test_moneyness_band_widens_with_time():
    assert option_capture.moneyness_band(0) == 0.10
    assert option_capture.moneyness_band(7 / 365) == pytest.approx(0.163, abs=0.001)
    assert option_capture.moneyness_band(30 / 365) == pytest.approx(0.252, abs=0.001)
    assert option_capture.moneyness_band(2.0) == 0.60


def test_capture_expiries_picks_the_nearest_listed_per_target():
    listed = [TODAY + timedelta(days=d) for d in (0, 3, 10, 17, 31, 59, 94, 185, 360, 725)]
    picked = [(e - TODAY).days for e in option_capture.capture_expiries(listed, TODAY)]
    # 7->10, 14->17, 30->31, 45->31 (tie with 59: the first), 60->59, 90->94,
    # 180->185, 365->360, 540->360 (180 away beats 185). Today's expiry is skipped.
    assert picked == [10, 17, 31, 59, 94, 185, 360]
    assert option_capture.capture_expiries([], TODAY) == []


def test_capture_universe_stores_live_slices_with_spot(sqlite_db, db_session, fake_yf):
    result = option_capture.capture_universe(["AAA", "BBB"], today=TODAY)
    assert not result.market_closed and not result.failed
    assert set(result.written) == {"AAA", "BBB"}
    rows = db_session.query(OptionQuote).filter_by(underlying="AAA").all()
    assert len(rows) == result.written["AAA"]
    assert all(r.underlying_price == SPOT for r in rows)
    assert all(r.bid == 0.95 and r.ask == 1.05 for r in rows)
    # Strikes are kept only within the band for their expiry.
    for r in rows:
        days = (r.expiration.date() - TODAY).days
        band = option_capture.moneyness_band(days / 365)
        assert SPOT * (1 - band) <= r.strike <= SPOT * (1 + band)
    assert min(r.strike for r in rows) > 20 and max(r.strike for r in rows) < 200  # the far wings are gone


def test_capture_stops_cleanly_when_the_market_is_closed(sqlite_db, db_session, fake_yf):
    fake_yf.market_state = "CLOSED"
    result = option_capture.capture_universe(["AAA", "BBB"], today=TODAY)
    assert result.market_closed and not result.written
    assert db_session.query(OptionQuote).count() == 0  # nothing off-hours is stored


def test_one_failing_symbol_does_not_stop_the_rest(sqlite_db, db_session, fake_yf):
    fake_yf.broken = {"BAD"}
    result = option_capture.capture_universe(["BAD", "AAA"], today=TODAY, retries=1)
    assert "BAD" in result.failed and "rate limited" in result.failed["BAD"]
    assert result.written["AAA"] > 0


def test_require_live_raises_before_storing(sqlite_db, db_session, fake_yf, monkeypatch):
    fake_yf.market_state = "PRE"
    monkeypatch.setattr(options.yf, "Ticker", FakeTicker)
    with pytest.raises(options.MarketClosedError):
        options.fetch_option_chain("AAA", TODAY + timedelta(days=31), require_live=True)
    assert db_session.query(OptionQuote).count() == 0


def test_sync_missing_columns_adds_new_model_columns():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE option_quotes DROP COLUMN underlying_price")
    assert "underlying_price" not in {c["name"] for c in inspect(engine).get_columns("option_quotes")}
    assert _sync_missing_columns(engine) == ["option_quotes.underlying_price"]
    assert "underlying_price" in {c["name"] for c in inspect(engine).get_columns("option_quotes")}
    assert _sync_missing_columns(engine) == []  # idempotent
