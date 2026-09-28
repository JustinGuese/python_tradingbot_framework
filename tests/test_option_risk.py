"""Stress scenarios and the daily option-risk snapshot."""

from datetime import datetime, timedelta

import pytest

from tradingbot.utils import option_math as om
from tradingbot.utils import option_risk, options
from tradingbot.utils.db import Bot, OptionQuote, OptionRiskSnapshot, Trade

S, R = 100.0, 0.04


def test_stress_signs_match_the_structure():
    short_put = [om.StressLeg("P", 95.0, -100, 30 / 365, 0.25)]
    grid = om.stress_pnl(short_put, S, R)
    assert grid.loc[-0.20, 0.0] < grid.loc[-0.05, 0.0] < 0 < grid.loc[0.10, 0.0]
    assert grid.loc[-0.05, 0.10] < grid.loc[-0.05, 0.0]  # short vega

    straddle = [om.StressLeg("C", 100.0, 100, 30 / 365, 0.2), om.StressLeg("P", 100.0, 100, 30 / 365, 0.2)]
    g = om.stress_pnl(straddle, S, R, spot_shocks=(0.0,), vol_shocks=(0.0, 0.10))
    assert g.loc[0.0, 0.0] == pytest.approx(0.0) and g.loc[0.0, 0.10] > 0

    shares = [om.StressLeg("S", 0.0, 50)]
    assert om.stress_pnl(shares, S, R, spot_shocks=(-0.10,)).loc[-0.10, 0.0] == pytest.approx(-500.0)


def test_crash_is_bounded_for_a_spread():
    spread = [om.StressLeg("P", 95.0, -100, 30 / 365, 0.25), om.StressLeg("P", 90.0, 100, 30 / 365, 0.27)]
    crash = om.worst_stress(spread, S, R)
    assert -500.0 <= crash < 0  # never worse than the $5 width x 100 shares


def test_snapshot_option_risk(sqlite_db, db_session, monkeypatch):
    monkeypatch.setattr(options, "dividend_yield", lambda u: 0.0)
    expiry = options.utc_today() + timedelta(days=30)
    short_k, long_k = f"AAPL{expiry:%y%m%d}P00095000", f"AAPL{expiry:%y%m%d}P00090000"
    db_session.add(Bot(name="option_TestBot", portfolio={"USD": 99_000.0, short_k: -100.0, long_k: 100.0}))
    db_session.add(Bot(name="NotAnOptionBot", portfolio={"USD": 10_000.0}))
    now = datetime.now().replace(microsecond=0)
    for key, K, px in [(short_k, 95.0, 1.60), (long_k, 90.0, 0.60)]:
        db_session.add(
            OptionQuote(
                underlying="AAPL",
                contract_symbol=key,
                expiration=datetime.combine(expiry, datetime.min.time()),
                option_type="P",
                strike=K,
                bid=px - 0.05,
                ask=px + 0.05,
                last_price=px,
                snapshot_at=now,
            )
        )
        db_session.add(
            Trade(
                bot_name="option_TestBot",
                symbol=key,
                isBuy=K == 90.0,
                quantity=100.0,
                price=px + (0.1 if K == 90.0 else -0.1),
                timestamp=now,
            )
        )
    db_session.commit()

    result = option_risk.snapshot_option_risk(get_spot=lambda u: S, r=R)
    assert result == {"written": ["option_TestBot/AAPL"], "failed": []}
    option_risk.snapshot_option_risk(get_spot=lambda u: S, r=R)  # idempotent
    row = db_session.query(OptionRiskSnapshot).one()
    assert row.delta_usd > 0 and row.vega < 0 and row.theta > 0  # short put spread
    assert row.stress_down20 < 0 < row.stress_up10
    assert row.margin == pytest.approx(500.0)  # the $5 width x 100
    assert row.stress_crash >= -500.0 - 1e-6
