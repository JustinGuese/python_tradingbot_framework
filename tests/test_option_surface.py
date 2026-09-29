"""The per-day vol surface that prices legs a sampled history did not record."""

import math
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils import option_math as om
from tradingbot.utils import option_surface as surf
from tradingbot.utils import options
from tradingbot.utils.db import OptionQuote
from tradingbot.utils.option_replay import ReplayMarket, SurfaceReplayMarket, live_expiry

DAY = date(2024, 3, 4)
SPOT, R, Q = 500.0, 0.04, 0.0
EXPIRIES = [DAY + timedelta(days=d) for d in (14, 30, 60)]
STRIKES = np.arange(400.0, 600.1, 10.0)


def true_iv(strike: float, T: float) -> float:
    """A skewed smile that is fixed in the surface's standardised moneyness."""
    z = math.log(strike / (SPOT * math.exp((R - Q) * T))) / T**surf.MONEYNESS_POWER
    return 0.18 - 0.08 * z + 0.05 * z * z


def _chain(expiries=EXPIRIES, strikes=STRIKES) -> pd.DataFrame:
    rows = []
    for expiry in expiries:
        T = om.year_fraction(expiry, DAY)
        for K in strikes:
            for right in ("C", "P"):
                px = om.bs_price(SPOT, K, T, R, true_iv(K, T), right, Q)
                if px < 0.05:
                    continue
                half = 0.01 if px < 2 else 0.10
                rows.append(
                    {
                        "option_type": right,
                        "strike": K,
                        "bid": px - half,
                        "ask": px + half,
                        "last_price": None,
                        "expiry": expiry,
                    }
                )
    return pd.DataFrame(rows)


def test_recovers_a_held_out_expiry_and_strike():
    """Drop the 30-day expiry: the surface built from 14 and 60 days prices it back."""
    chain = _chain()
    s = surf.build_surface("SPY", chain[chain.expiry != EXPIRIES[1]], SPOT, DAY, R, Q)
    assert s is not None and len(s.slices) == 2
    T = om.year_fraction(EXPIRIES[1], DAY)
    for K in (455.0, 480.0, 505.0, 530.0):  # off the stored strike grid, inside the quoted range
        assert s.iv(K, T) == pytest.approx(true_iv(K, T), abs=0.005)
    # Before the first expiry: the first slice's smile at the same standardised moneyness.
    T7 = 7 / 365
    assert s.iv(480.0, T7) == pytest.approx(true_iv(480.0, T7), abs=0.01)


def test_quotes_are_ordered_floored_at_intrinsic_and_spread_like_their_neighbours():
    s = surf.build_surface("SPY", _chain(), SPOT, DAY, R, Q)
    expiry = EXPIRIES[1]
    bid, ask, mid = s.quote("P", 450.0, expiry)  # a cheap wing: the 0.01 half-spreads nearby
    assert 0 <= bid <= mid <= ask and ask - bid == pytest.approx(0.02, abs=1e-9)
    bid, ask, mid = s.quote("C", 500.0, expiry)  # at the money: 0.10 half-spreads
    assert ask - bid == pytest.approx(0.20, abs=1e-9)
    assert s.quote("C", 450.0, DAY) == (50.0, 50.0, 50.0)  # expiring today: intrinsic
    _, _, deep = s.quote("P", 700.0, expiry)
    assert deep >= 200.0  # never below intrinsic


def test_too_thin_a_chain_has_no_surface():
    chain = _chain(expiries=[EXPIRIES[0]], strikes=[500.0])
    assert surf.build_surface("SPY", chain, SPOT, DAY, R, Q) is None


@pytest.fixture
def sampled(sqlite_db, db_session):
    """Two days of a sampled chain: day 2 records the 60-day expiry only, and not the strikes day 1 did."""
    snap = lambda d: datetime.combine(d, datetime.min.time()) + timedelta(hours=21)  # noqa: E731
    day2 = DAY + timedelta(days=2)
    for day, expiries, strikes in (
        (DAY, EXPIRIES, STRIKES),
        (day2, [EXPIRIES[2]], np.arange(405.0, 600.1, 10.0)),
    ):
        for row in _chain(expiries, strikes).itertuples():
            db_session.add(
                OptionQuote(
                    underlying="SPY",
                    contract_symbol=f"SPY{row.expiry:%y%m%d}{row.option_type}{round(row.strike * 1000):08d}",
                    expiration=datetime.combine(row.expiry, datetime.min.time()),
                    option_type=row.option_type,
                    strike=row.strike,
                    bid=row.bid,
                    ask=row.ask,
                    underlying_price=SPOT,
                    snapshot_at=snap(day),
                )
            )
    db_session.commit()
    return day2


def test_surface_market_prices_what_the_day_did_not_record(sampled, monkeypatch):
    monkeypatch.setattr("tradingbot.utils.options.risk_free_rate", lambda: R)
    monkeypatch.setattr("tradingbot.utils.options.dividend_yield", lambda _u: Q)
    held = f"SPY{EXPIRIES[1]:%y%m%d}P00480000"  # recorded on day 1 only
    plain = ReplayMarket(DAY, sampled)
    assert plain.quote(held, sampled) == (None, None, None)  # the gap a plain replay freezes on

    m = SurfaceReplayMarket(DAY, sampled)
    recorded = m.quote(held, DAY)
    assert recorded == plain.quote(held, DAY) and (m.recorded, m.modelled) == (1, 0)
    bid, ask, mid = m.quote(held, sampled)
    T = om.year_fraction(EXPIRIES[1], sampled)
    # Priced by extrapolating the 60-day slice inward, so looser than an interpolation.
    assert mid == pytest.approx(om.bs_price(SPOT, 480.0, T, R, true_iv(480.0, T), "P", Q), rel=0.1)
    assert bid < mid < ask and m.modelled == 1


def test_live_expiry_is_the_first_friday_past_the_target():
    assert live_expiry(date(2024, 3, 4), 35) == date(2024, 4, 12)  # Mon + 35 = Mon 4/8 -> Fri 4/12
    assert live_expiry(date(2024, 2, 23), 35) == date(2024, 3, 29) - timedelta(days=1)  # Good Friday -> Thursday


def test_live_expiries_view_lists_the_bots_expiry_off_the_surface(sampled, monkeypatch):
    monkeypatch.setattr("tradingbot.utils.options.risk_free_rate", lambda: R)
    monkeypatch.setattr("tradingbot.utils.options.dividend_yield", lambda _u: Q)
    m = SurfaceReplayMarket(DAY, sampled, live_expiries=True)
    view = m.view("SPY", DAY, 35)
    assert view.expiry == date(2024, 4, 12) and view.live and view.spot == SPOT
    assert set(view.frame["strike"]) >= {480.0, 481.0, 500.0}  # the $1 SPY grid, not the stored $10 one
    pick = options.select_iron_condor("SPY", 0.10, 50.0, 35, view=view)
    assert len(pick.legs) == 4 and m.modelled == 0  # building the chain is not counted as marking
