"""Vol-surface summary: IV solving, constant maturity, skew, options TA, storage."""

import math
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils import option_math as om
from tradingbot.utils import vol_surface as vs
from tradingbot.utils.db import ImpliedCorrelation, OptionQuote, VolSurfaceSnapshot

TODAY = date(2026, 9, 28)
S, R = 100.0, 0.03
DTES = (7, 21, 35, 63, 95, 190)


def _smile(K: float, T: float, skew: float) -> float:
    return 0.20 - skew * math.log(K / S)


def chain(skew: float = 0.0, oi: float = 100.0, volume: float = 10.0) -> pd.DataFrame:
    """Every strike 60..140 on six expiries, priced off a known smile, ±0.02 around fair."""
    rows = []
    for dte in DTES:
        expiry = TODAY + timedelta(days=dte)
        T = om.year_fraction(expiry, TODAY)
        for K in np.arange(60.0, 140.1, 2.5):
            for right in ("C", "P"):
                px = om.bs_price(S, K, T, R, _smile(K, T, skew), right)
                if px < 0.05:
                    continue
                rows.append(
                    {
                        "contract_symbol": f"X{dte}{right}{K}",
                        "option_type": right,
                        "strike": K,
                        "expiration": datetime.combine(expiry, datetime.min.time()),
                        "bid": px - 0.02,
                        "ask": px + 0.02,
                        "last_price": px,
                        "volume": volume,
                        "open_interest": oi,
                        "underlying_price": S,
                    }
                )
    return pd.DataFrame(rows)


def test_solve_ivs_recovers_the_smile():
    solved = vs.solve_ivs(chain(skew=0.3), S, TODAY, R)
    atm = solved[(solved["strike"] == 100.0)]
    assert atm["iv"].to_numpy() == pytest.approx(0.20, abs=0.005)
    assert (solved["dte"] >= vs.MIN_DTE).all()


def test_flat_surface_summary():
    row = vs.summarize(chain(), S, TODAY, R, fair_vol=0.15)
    for d in vs.CM_DAYS:
        assert row[f"atm_iv_{d}"] == pytest.approx(0.20, abs=0.006)
    assert row["rr25_30"] == pytest.approx(0.0, abs=0.01)
    assert row["term_slope"] == pytest.approx(1.0, abs=0.03)
    assert row["vrp_30"] == pytest.approx(0.05, abs=0.006)


def test_put_skew_shows_up_as_positive_risk_reversal():
    row = vs.summarize(chain(skew=0.3), S, TODAY, R)
    assert row["rr25_30"] == pytest.approx(0.023, abs=0.005)  # 25d strikes sit ~3.8% out
    assert row["iv_25p_30"] > row["atm_iv_30"] > row["iv_25c_30"]


def test_call_put_spread_is_zero_at_parity_and_positive_when_calls_are_rich():
    assert vs.summarize(chain(), S, TODAY, R)["cp_spread_30"] == pytest.approx(0.0, abs=0.003)
    rich = chain()
    calls = rich["option_type"] == "C"
    rich.loc[calls, ["bid", "ask", "last_price"]] += 0.10
    assert vs.summarize(rich, S, TODAY, R)["cp_spread_30"] > 0.005


def test_constant_maturity_is_linear_in_total_variance():
    pts = [(20 / 365, 0.30), (40 / 365, 0.20)]
    w = (0.30**2 * 20 + 0.20**2 * 40) / 2 / 365
    assert vs.constant_maturity_iv(pts, 30) == pytest.approx(math.sqrt(w / (30 / 365)))
    assert vs.constant_maturity_iv(pts, 5) == 0.30  # flat in vol outside
    assert vs.constant_maturity_iv(pts, 400) == 0.20
    assert vs.constant_maturity_iv([], 30) is None


def test_options_ta():
    frame = chain(oi=100.0, volume=10.0)
    pc_vol, pc_oi = vs.put_call_ratios(frame)
    assert pc_vol == pytest.approx(pc_oi)  # same volume and OI on every listed contract
    assert vs.unusual_activity(frame).empty
    frame.loc[0, ["volume", "open_interest"]] = [900, 100]
    assert len(vs.unusual_activity(frame)) == 1

    solved = vs.solve_ivs(chain(), S, TODAY, R)
    calls_only = solved[solved["option_type"] == "C"]
    assert vs.gamma_exposure(calls_only, S) > 0
    assert vs.gamma_exposure(solved[solved["option_type"] == "P"], S) < 0

    one = pd.DataFrame(
        {
            "strike": [90.0, 100.0, 110.0, 90.0, 100.0, 110.0],
            "option_type": list("CCCPPP"),
            "open_interest": [0, 0, 500, 500, 0, 0],
        }
    )
    # Calls at 110 and puts at 90 both expire worthless anywhere in 90..110.
    assert vs.max_pain(one) in (90.0, 100.0, 110.0)
    assert vs.max_pain(one.assign(open_interest=0)) is None


def test_implied_correlation():
    assert vs.implied_correlation(0.30, [0.30] * 5, [1] * 5) == pytest.approx(1.0)
    assert vs.implied_correlation(0.15, [0.30] * 5, [1] * 5) < 0.3
    assert vs.implied_correlation(0.2, [0.3], [1]) is None


def _store_quotes(db_session, frame: pd.DataFrame, underlying: str, day: date) -> None:
    snap = datetime.combine(day, datetime.min.time()) + timedelta(hours=19, minutes=45)
    for row in frame.to_dict("records"):
        db_session.add(
            OptionQuote(
                underlying=underlying,
                contract_symbol=f"{underlying}{row['contract_symbol']}",
                expiration=row["expiration"],
                option_type=row["option_type"],
                strike=row["strike"],
                bid=row["bid"],
                ask=row["ask"],
                last_price=row["last_price"],
                volume=row["volume"],
                open_interest=row["open_interest"],
                underlying_price=row["underlying_price"],
                snapshot_at=snap,
            )
        )
    db_session.commit()


def test_snapshot_vol_surface_writes_one_idempotent_row(sqlite_db, db_session):
    _store_quotes(db_session, chain(skew=0.2), "AAPL", TODAY)
    rng = np.random.default_rng(1)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 600))))
    for _ in range(2):
        result = vs.snapshot_vol_surface(TODAY, closes={"AAPL": close}, r=R, dividend_yield=lambda u: 0.0)
    assert result == {"written": ["AAPL"], "failed": []}
    rows = db_session.query(VolSurfaceSnapshot).all()
    assert len(rows) == 1 and rows[0].atm_iv_30 == pytest.approx(0.20, abs=0.01)
    assert rows[0].fair_vol_30 == pytest.approx(0.16, abs=0.04)

    latest = vs.latest_surface("AAPL", as_of=TODAY)
    assert latest["rr25_30"] > 0
    assert vs.surface_rank("AAPL", "atm_iv_30", 0.25, as_of=TODAY) is None  # 1 obs < 60
    assert vs.captured_dates() == [TODAY]


def test_a_backfilled_day_fits_fair_vol_only_on_closes_up_to_it(sqlite_db, db_session):
    """Handing the backfill the whole history must give the same row as the history the day had."""
    _store_quotes(db_session, chain(skew=0.2), "AAPL", TODAY)
    idx = pd.bdate_range(end=TODAY + timedelta(days=200), periods=1200)
    rng = np.random.default_rng(2)
    rets = np.where(idx.date <= TODAY, rng.normal(0, 0.01, len(idx)), rng.normal(0, 0.05, len(idx)))
    close = pd.Series(100 * np.exp(np.cumsum(rets)), index=idx)  # calm until TODAY, wild after

    def fair(closes):
        vs.snapshot_vol_surface(TODAY, closes={"AAPL": closes}, r=R, dividend_yield=lambda u: 0.0)
        return db_session.query(VolSurfaceSnapshot).one().fair_vol_30

    with_future, as_of = fair(close), fair(close[close.index.date <= TODAY])
    assert with_future == pytest.approx(as_of)
    assert as_of == pytest.approx(0.16, abs=0.04)  # the calm regime, not the later 5%/day one
    window = vs.close_window(close, TODAY)
    assert window.index[-1].date() == TODAY
    assert window.index[0] > pd.Timestamp(TODAY) - pd.DateOffset(years=vs.CLOSE_HISTORY_YEARS)


def test_backfill_rate_is_the_one_known_on_the_day():
    from tradingbot.optionchainsnapshot import rate_on

    irx = pd.Series([5.0, 4.0, 3.0], index=pd.to_datetime(["2025-01-02", "2025-06-02", "2026-09-01"]))
    assert rate_on(irx, date(2025, 3, 1)) == pytest.approx(0.05)
    assert rate_on(irx, date(2026, 9, 1)) == pytest.approx(0.03)
    assert rate_on(irx, date(2024, 12, 31)) is None


def test_snapshot_implied_correlation(sqlite_db, db_session):
    names = [f"N{i}" for i in range(12)]
    for u, iv in [("SPY", 0.15), *[(n, 0.30) for n in names]]:
        db_session.add(VolSurfaceSnapshot(underlying=u, snapshot_date=TODAY, atm_iv_30=iv))
    db_session.commit()
    value = vs.snapshot_implied_correlation(TODAY, weights=dict.fromkeys(names, 1.0))
    stored = db_session.query(ImpliedCorrelation).one()
    assert stored.value == pytest.approx(value) and 0 < value < 0.3
    assert stored.n_names == 12
    assert vs.implied_correlation_history(as_of=TODAY).tolist() == pytest.approx([value])


def test_names_without_a_fundamentals_cap_take_the_historical_one(sqlite_db, db_session):
    names = [f"N{i}" for i in range(12)]
    for u, iv in [("SPY", 0.15), *[(n, 0.30) for n in names]]:
        db_session.add(VolSurfaceSnapshot(underlying=u, snapshot_date=TODAY, atm_iv_30=iv))
    db_session.commit()
    assert vs.snapshot_implied_correlation(TODAY) is None  # stock_fundamentals has no row that far back
    value = vs.snapshot_implied_correlation(TODAY, fallback_weights=dict.fromkeys(names, 1.0))
    assert value == pytest.approx(vs.implied_correlation(0.15, [0.30] * 12, [1.0] * 12))


def test_backfill_caps_are_the_last_known_on_the_day():
    from tradingbot.optionchainsnapshot import caps_on

    caps = pd.DataFrame({"A": [1.0, 2.0], "B": [np.nan, 3.0]}, index=pd.to_datetime(["2025-01-02", "2025-01-03"]))
    assert caps_on(caps, date(2025, 1, 2)) == {"A": 1.0}
    assert caps_on(caps, date(2025, 1, 5)) == {"A": 2.0, "B": 3.0}
    assert caps_on(caps, date(2024, 12, 31)) == {}
    assert caps_on(pd.DataFrame(), date(2025, 1, 5)) == {}


def test_reported_share_counts_are_restated_across_splits():
    """yfinance files NVDA at 610M shares in 2021 and 24.5B after its 4:1 and 10:1 splits."""
    from tradingbot.utils.fundamentals import split_adjusted_shares

    tz = "America/New_York"
    when = pd.to_datetime(["2021-01-04", "2021-06-01", "2021-09-01", "2024-03-01", "2024-09-01"]).tz_localize(tz)
    shares = pd.Series([610e6, 612e6, 2.45e9, 2.46e9, 24.5e9], index=when)
    splits = pd.Series([4.0, 10.0], index=pd.to_datetime(["2021-07-20", "2024-06-10"]).tz_localize(tz))
    out = split_adjusted_shares(shares, splits)
    assert out.min() > 24.0e9 and out.max() < 25.0e9


def test_snapshot_scan_writes_vrp_rows_and_replaces_the_day(sqlite_db, db_session):
    from tradingbot.utils import mispricing_scan as ms
    from tradingbot.utils.db import MispricingScanRow

    _store_quotes(db_session, chain(skew=0.2), "AAPL", TODAY)
    for i in range(80):  # 80 past days of vrp around 0 +/- 1 pt, then today's surface
        db_session.add(
            VolSurfaceSnapshot(underlying="AAPL", snapshot_date=TODAY - timedelta(days=81 - i), vrp_30=0.01 * (-1) ** i)
        )
    db_session.add(
        VolSurfaceSnapshot(underlying="AAPL", snapshot_date=TODAY, atm_iv_30=0.2, fair_vol_30=0.15, vrp_30=0.05)
    )
    db_session.commit()
    n = ms.snapshot_scan(TODAY, r=R, dividend_yield=lambda u: 0.0)
    assert ms.snapshot_scan(TODAY, r=R, dividend_yield=lambda u: 0.0) == n  # rerun replaces, no duplicates
    rows = db_session.query(MispricingScanRow).filter_by(scan_date=TODAY).all()
    assert len(rows) == n
    (vrp,) = [r for r in rows if r.kind == "vrp"]
    assert vrp.z == pytest.approx(5.0, rel=0.05)  # (0.05 - 0) / 0.01
    assert ms.latest_scan_scores(as_of=TODAY) == {"AAPL": pytest.approx(vrp.z)}
