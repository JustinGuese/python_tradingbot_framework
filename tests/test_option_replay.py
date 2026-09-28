"""Replay backtests over stored option_quotes: views, fills at bid/ask, marks, settlement."""

from datetime import date, datetime, timedelta

import numpy as np
import pytest

from tradingbot.utils import option_math as om
from tradingbot.utils import options
from tradingbot.utils.db import OptionQuote
from tradingbot.utils.option_replay import ReplayBook, ReplayMarket

D0 = date(2026, 10, 5)  # Monday
DAYS = [D0 + timedelta(days=i) for i in range(4)]
EXPIRY = D0 + timedelta(days=2)  # expires mid-replay
LATER = D0 + timedelta(days=40)
R, SIG = 0.04, 0.25
SPOTS = [100.0, 102.0, 95.0, 96.0]


def _occ(expiry: date, right: str, strike: float) -> str:
    return f"AAPL{expiry:%y%m%d}{right}{round(strike * 1000):08d}"


@pytest.fixture
def stored(sqlite_db, db_session):
    for day, spot in zip(DAYS, SPOTS, strict=True):
        snap = datetime.combine(day, datetime.min.time()) + timedelta(hours=19, minutes=45)
        for expiry in (EXPIRY, LATER):
            if expiry < day:
                continue
            T = max(om.year_fraction(expiry, day), 1 / 365)
            for K in np.arange(70.0, 130.1, 2.5):
                for right in ("C", "P"):
                    px = om.bs_price(spot, K, T, R, SIG, right)
                    if px < 0.05:
                        continue
                    db_session.add(
                        OptionQuote(
                            underlying="AAPL",
                            contract_symbol=_occ(expiry, right, K),
                            expiration=datetime.combine(expiry, datetime.min.time()),
                            option_type=right,
                            strike=K,
                            bid=px - 0.05,
                            ask=px + 0.05,
                            last_price=px,
                            underlying_price=spot,
                            snapshot_at=snap,
                        )
                    )
    db_session.commit()


def test_market_views_look_like_live_chains(stored):
    m = ReplayMarket(DAYS[0], DAYS[-1])
    assert m.days == DAYS and m.underlyings(D0) == ["AAPL"]
    view = m.view("AAPL", D0, 30)
    assert view.expiry == LATER and view.live and view.spot == 100.0
    assert m.spot("AAPL", DAYS[2]) == 95.0
    assert m.spot("AAPL", D0 - timedelta(days=1)) is None


def test_book_fills_at_bid_ask_marks_at_mid_and_settles(stored, monkeypatch):
    monkeypatch.setattr(options, "risk_free_rate", lambda: R)
    monkeypatch.setattr(options, "dividend_yield", lambda _u: 0.0)
    m = ReplayMarket(DAYS[0], DAYS[-1])
    book = ReplayBook(m, cash=10_000.0)

    near = m.view("AAPL", D0, 1)
    assert near.expiry == EXPIRY
    pick = options.select_straddle(near)
    (call, _), (put, _) = pick.legs
    _bid_c, ask_c, mid_c = m.quote(call, D0)
    _bid_p, ask_p, mid_p = m.quote(put, D0)
    assert book.open(pick, 1, D0)
    assert book.cash == pytest.approx(10_000.0 - 100 * (ask_c + ask_p))  # paid the ask
    assert book.equity(D0) == pytest.approx(book.cash + 100 * (mid_c + mid_p))  # marked at mid

    # After expiry the straddle pays intrinsic off the recorded spot on the expiry day (95).
    book.settle(DAYS[3])
    strike = options.parse_occ(call).strike
    assert book.positions == {}
    assert book.cash == pytest.approx(10_000.0 - 100 * (ask_c + ask_p) + 100 * abs(95.0 - strike))


def test_close_sells_at_the_bid(stored):
    m = ReplayMarket(DAYS[0], DAYS[-1])
    book = ReplayBook(m, cash=10_000.0)
    view = m.view("AAPL", D0, 30)
    pick = options.select_straddle(view)
    book.open(pick, 1, D0)
    book.close("AAPL", DAYS[1])
    (call, _), (put, _) = pick.legs
    assert book.positions == {}
    got = book.cash - 10_000.0
    paid = 100 * (m.quote(call, D0)[1] + m.quote(put, D0)[1])
    received = 100 * (m.quote(call, DAYS[1])[0] + m.quote(put, DAYS[1])[0])
    assert got == pytest.approx(received - paid)


def test_empty_table_has_no_days(sqlite_db):
    assert ReplayMarket(D0, DAYS[-1]).days == []
