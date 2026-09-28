"""Live alpha vs QQQ (utils/alpha_report.py): cleaning, the formula, verdicts and the weekly job."""

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils import alpha_report as ar
from tradingbot.utils.db import Bot, BotAlphaReport, PortfolioWorth

TODAY = date(2026, 9, 26)  # a Saturday, like the CronJob


def _worth_from(rets: pd.Series, start: float = 10_000.0) -> pd.Series:
    """Weekday returns -> a daily worth series where weekend rows repeat Friday."""
    levels = start * (1 + rets).cumprod()
    return levels.reindex(pd.date_range(levels.index[0], levels.index[-1], freq="D")).ffill()


def test_alpha_stats_matches_the_claude_md_formula():
    rng = np.random.default_rng(0)
    b = pd.Series(rng.normal(0.0005, 0.01, 250))
    p = 0.3 * b + 0.0004 + pd.Series(rng.normal(0, 0.004, 250))
    s = ar.alpha_stats(p, b)
    beta = p.cov(b) / b.var()
    resid = p - beta * b
    assert s.beta == pytest.approx(beta)
    assert s.alpha == pytest.approx(resid.mean() * 252)
    assert s.t == pytest.approx(resid.mean() / resid.std() * np.sqrt(250))
    assert s.n == 250 and 0 < s.corr < 1
    assert ar.alpha_stats(p[:2], b[:2]) is None  # nothing to measure is not "zero alpha"


def test_weekends_and_recorder_gaps_are_dropped():
    idx = pd.to_datetime(["2026-09-04", "2026-09-05", "2026-09-06", "2026-09-07", "2026-09-08", "2026-09-15"])
    worth = pd.Series([100.0, 100.0, 100.0, 101.0, 102.0, 110.0], index=idx)  # Fri, Sat, Sun, Mon, Tue, +7d gap
    levels = ar.weekday_levels(worth)
    assert list(levels.index.dayofweek) == [4, 0, 1, 1]
    glitch = worth.copy()
    glitch.iloc[3] = 0.0  # a failed valuation, not a -100% day
    assert len(ar.weekday_levels(glitch)) == 3
    rets = ar.joint_returns(worth, worth)
    # Fri->Mon (3 days) is a return; Tue->next Tue (7 days) is an outage.
    assert list(rets.index.date) == [date(2026, 9, 7), date(2026, 9, 8)]


def test_verdicts():
    def s(t, beta=0.1, corr=0.1, n=100):
        return ar.AlphaStats(alpha=0.0, t=t, beta=beta, corr=corr, n=n)

    assert ar.verdict(None) == "too short"
    assert ar.verdict(s(3.0, n=30)) == "too short"
    assert ar.verdict(s(2.1)) == "edge"
    assert ar.verdict(s(-2.3)) == "pause candidate"
    assert ar.verdict(s(0.2, beta=0.95, corr=0.9)) == "QQQ clone"
    assert ar.verdict(s(-1.9, beta=2.6, corr=0.94)) == "levered QQQ"
    assert ar.verdict(s(0.9)) == "unproven"


def _series(n, seed, fn):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(end=TODAY - timedelta(days=1), periods=n)  # the Friday before TODAY
    q = pd.Series(rng.normal(0.0006, 0.012, n), index=days)
    return q, fn(q, rng)


def test_build_report_ranks_by_t_and_flags_the_bleeder():
    q, good = _series(200, 1, lambda q, rng: 0.1 * q + 0.001 + rng.normal(0, 0.003, len(q)))
    _, bad = _series(200, 1, lambda q, rng: 0.2 * q - 0.0012 + rng.normal(0, 0.003, len(q)))
    _, clone = _series(200, 1, lambda q, rng: q + rng.normal(0, 0.001, len(q)))
    old = _worth_from(q).loc[: pd.Timestamp(TODAY) - pd.Timedelta(days=30)]  # stopped a month ago
    worth = {
        "Benchmark_QQQ": _worth_from(q),
        "Benchmark_SPY": _worth_from(q),
        "Good": _worth_from(good),
        "Bad": _worth_from(bad),
        "Clone": _worth_from(clone),
        "Gone": old,
    }
    rows = ar.build_report(worth, TODAY)
    assert rows[0].bot_name == "Good" and rows[-1].bot_name == "Bad"
    by = {r.bot_name: r for r in rows}
    assert set(by) == {"Good", "Bad", "Clone"}  # benchmarks and dead bots are not reported
    assert by["Good"].verdict == "edge" and by["Bad"].verdict == "pause candidate"
    assert by["Clone"].verdict == "QQQ clone"
    assert by["Good"].max_dd <= 0 and by["Good"].window_end == TODAY - timedelta(days=1)  # Friday


def test_a_stale_benchmark_refuses_to_report():
    q, good = _series(100, 2, lambda q, rng: q)
    with pytest.raises(LookupError, match="stale"):
        ar.build_report({"Benchmark_QQQ": _worth_from(q), "Good": _worth_from(good)}, TODAY + timedelta(days=10))
    with pytest.raises(LookupError, match="No Benchmark_QQQ"):
        ar.build_report({"Good": _worth_from(good)}, TODAY)


def test_weekly_job_writes_one_row_per_bot(sqlite_db, db_session, monkeypatch):
    monkeypatch.setattr("tradingbot.utils.alpha_report.get_db_session", sqlite_db)
    q, good = _series(120, 3, lambda q, rng: 0.2 * q + rng.normal(0, 0.004, len(q)))
    for name, worth in {"Benchmark_QQQ": _worth_from(q), "Good": _worth_from(good)}.items():
        db_session.add(Bot(name=name, portfolio={"USD": 0.0}))
        for d, v in worth.items():
            db_session.add(
                PortfolioWorth(
                    bot_name=name,
                    date=datetime.combine(d.date(), datetime.min.time()),
                    portfolio_worth=float(v),
                    holdings={},
                )
            )
    db_session.commit()
    ar.run_weekly_report(TODAY)
    ar.run_weekly_report(TODAY)  # idempotent: upserts
    (row,) = db_session.query(BotAlphaReport).all()
    assert row.bot_name == "Good" and row.n_obs == 119 and row.verdict in {"unproven", "edge", "QQQ clone"}
    assert row.report_date == TODAY and row.alpha_t is not None
