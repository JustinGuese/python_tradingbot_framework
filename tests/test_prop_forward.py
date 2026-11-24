"""Forward paper prop challenges (utils/prop_forward.py, propsimlive job)."""

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from tradingbot import propsimlive
from tradingbot.utils import prop_forward as pf
from tradingbot.utils.db import Bot, PortfolioWorth, PropChallengeSim
from tradingbot.utils.prop_challenge import PRESETS


def _daily(rets: list[float], start: str, name_level: float = 10_000.0) -> pd.Series:
    """Weekday returns -> a calendar-daily worth series whose weekend rows repeat Friday."""
    idx = pd.bdate_range(start, periods=len(rets))
    levels = pd.Series(name_level * np.cumprod(1.0 + np.array(rets)), index=idx)
    return levels.reindex(pd.date_range(idx[0], idx[-1], freq="D")).ffill()


def test_track_breach_and_phase_hand_over():
    ftmo = PRESETS["ftmo_2step_100k"]
    # 4 days of +3% beat the 10% target with min_days 4: phase 1 passes, phase 2 starts fresh.
    st = pf.track(np.array([0.03, 0.03, 0.03, 0.03]), ftmo)
    assert (st.stage, st.status, st.stage_day, st.trading_days) == ("phase 2", "running", 0, 4)
    assert st.equity == 1.0 and st.target_equity == pytest.approx(1.05)
    # A -6% day breaches the 5% daily-loss rule, and the breach is final for that stage.
    dead = pf.track(np.array([0.01, -0.06]), ftmo)
    assert (dead.stage, dead.status, dead.trading_days) == ("phase 1", "breached", 2)
    # Still running inside the limits, with headroom reported against the static limit.
    live = pf.track(np.array([0.01, -0.02]), ftmo)
    assert live.status == "running" and live.limit_equity == pytest.approx(0.90)


def test_source_returns_weights_weekends_gaps_and_start():
    a = _daily([0.01, 0.02, -0.01, 0.00, 0.03, 0.01, 0.02], "2026-11-02")
    b = _daily([0.00, 0.01, 0.01, 0.02, -0.02, 0.01, 0.00], "2026-11-02")
    worth = {"A": a, "B": b}
    r = pf.source_returns(worth, {"A": 0.5, "B": 0.5})
    assert (r.index.dayofweek < 5).all() and len(r) == 6
    assert r.iloc[0] == pytest.approx(0.5 * 0.02 + 0.5 * 0.01)
    since = pf.source_returns(worth, {"A": 0.5, "B": 0.5}, since=date(2026, 11, 6))
    assert since.index[0] == pd.Timestamp("2026-11-06")
    # A 6-day recorder outage is not a day's move.
    gap = pd.concat([a.iloc[:5], a.iloc[12:]])
    assert len(pf.source_returns({"A": gap}, {"A": 1.0})) < len(pf.source_returns({"A": a}, {"A": 1.0}))
    with pytest.raises(LookupError):
        pf.source_returns(worth, {"A": 0.5, "Missing": 0.5})


def test_daily_job_upserts_and_refuses_stale_data(sqlite_db, db_session, monkeypatch):
    monkeypatch.setattr("tradingbot.propsimlive.get_db_session", sqlite_db)
    monkeypatch.setattr("tradingbot.propsimlive.load_irx", lambda start=None: pd.Series(dtype=float))
    rng = np.random.default_rng(1)
    names = [*pf.BLEND_WEIGHTS, pf.BENCHMARK]
    for name in names:
        db_session.add(Bot(name=name, portfolio={"USD": 0.0}))
        for d, v in _daily(list(rng.normal(0.001, 0.004, 14)), "2026-11-09").items():
            db_session.add(
                PortfolioWorth(
                    bot_name=name,
                    date=datetime.combine(d.date(), datetime.min.time()),
                    portfolio_worth=float(v),
                    holdings={},
                )
            )
    db_session.commit()
    today = date(2026, 11, 27)
    propsimlive.run_daily(today, start=date(2026, 11, 10))
    propsimlive.run_daily(today, start=date(2026, 11, 10))  # idempotent: upserts, no duplicates
    rows = db_session.query(PropChallengeSim).all()
    assert len(rows) == len(pf.SIMS) == 6
    assert {r.status for r in rows} <= {"running", "breached", "funded"} and all(r.trading_days > 0 for r in rows)
    with pytest.raises(LookupError):
        propsimlive.run_daily(date(2026, 12, 30), start=date(2026, 11, 10))
