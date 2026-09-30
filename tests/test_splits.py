"""Stock splits: detection, cached-history rescaling, and book adjustment (utils/splits.py)."""

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils import market_calendar, options, splits
from tradingbot.utils.db import AppliedSplit, Bot, HistoricData, SplitEvent, Trade
from tradingbot.utils.splits import Split

EX = date(2026, 4, 21)  # VGT's 8:1 split, a Tuesday
TODAY = EX + timedelta(days=2)


def _bot(session, name="B", portfolio=None):
    session.add(Bot(name=name, portfolio=portfolio or {"USD": 1000.0}))
    session.commit()


def _event(session, symbol="VGT", ex=EX, ratio=8.0, pre_close=809.17):
    session.add(SplitEvent(symbol=symbol, ex_date=ex, ratio=ratio, pre_split_close=pre_close))
    session.commit()


def _trade(session, ts, symbol, qty, price, buy, bot="B"):
    session.add(Trade(bot_name=bot, symbol=symbol, quantity=qty, price=price, isBuy=buy, timestamp=ts))
    session.commit()


def _portfolio(session, name="B"):
    session.expire_all()
    return dict(session.query(Bot).filter_by(name=name).one().portfolio)


# ------------------------------------------------------------------
# Detection
# ------------------------------------------------------------------


def test_parse_split_frame_reads_ratio_and_pre_split_close():
    idx = pd.to_datetime(["2026-04-17", "2026-04-20", "2026-04-21", "2026-04-22"])
    cols = pd.MultiIndex.from_product([["VGT", "SPY"], ["Close", "Stock Splits"]])
    df = pd.DataFrame(
        [[100.5, 0.0, 700.0, 0.0], [101.0, 0.0, 701.0, 0.0], [100.8, 8.0, 702.0, 0.0], [102.0, 0.0, 703.0, np.nan]],
        index=idx,
        columns=cols,
    )
    out = splits.parse_split_frame(df)
    assert out == [Split("VGT", EX, 8.0, pytest.approx(101.0 * 8))]


def test_parse_split_frame_empty():
    assert splits.parse_split_frame(pd.DataFrame()) == []


# ------------------------------------------------------------------
# Cached history
# ------------------------------------------------------------------


def _bars(closes: dict[str, float]) -> pd.DataFrame:
    ts = pd.to_datetime(list(closes))
    c = list(closes.values())
    return pd.DataFrame({"timestamp": ts, "open": c, "close": c})


CUT = datetime(2026, 4, 21)


def test_boundary_at_the_ex_date_when_old_bars_were_never_rewritten():
    bars = _bars({"2026-04-16": 791.8, "2026-04-17": 805.6, "2026-04-20": 809.2, "2026-04-21": 101.0})
    assert splits.find_unadjusted_boundary(bars, CUT, 8.0) == datetime(2026, 4, 20)


def test_no_boundary_when_the_series_is_already_adjusted():
    bars = _bars({"2026-04-17": 100.7, "2026-04-20": 101.1, "2026-04-21": 101.0})
    assert splits.find_unadjusted_boundary(bars, CUT, 8.0) is None


def test_every_old_bar_is_old_scale_when_nothing_was_written_since():
    bars = _bars({"2026-04-17": 805.6, "2026-04-20": 809.2})
    assert splits.find_unadjusted_boundary(bars, CUT, 8.0) == datetime(2026, 4, 20)


def test_boundary_before_the_ex_date_when_a_later_fetch_appended_adjusted_bars():
    # Cache stopped on the 10th; the next fetch, after the split, appended adjusted bars from the 13th.
    bars = _bars({"2026-04-09": 780.0, "2026-04-10": 790.0, "2026-04-13": 99.0, "2026-04-20": 101.1, "2026-04-21": 101})
    assert splits.find_unadjusted_boundary(bars, CUT, 8.0) == datetime(2026, 4, 10)


def test_reverse_split_boundary():
    bars = _bars({"2025-11-19": 10.0, "2025-11-20": 50.5})
    assert splits.find_unadjusted_boundary(bars, datetime(2025, 11, 20), 0.2) == datetime(2025, 11, 19)


def test_adjust_history_rescales_old_rows_only_and_is_idempotent(db_session):
    rows = [
        ("1d", datetime(2026, 4, 17), 805.6, 1000.0),
        ("1d", datetime(2026, 4, 20), 809.2, 1000.0),
        ("1d", datetime(2026, 4, 21), 101.0, 8000.0),
        ("1m", datetime(2026, 4, 20, 19, 59), 809.0, 10.0),
        ("1m", datetime(2026, 4, 21, 13, 30), 101.1, 80.0),
    ]
    for interval, ts, c, v in rows:
        db_session.add(
            HistoricData(symbol="VGT", interval=interval, timestamp=ts, open=c, high=c, low=c, close=c, volume=v)
        )
    db_session.commit()

    assert splits.adjust_history(db_session, "VGT", EX, 8.0) == {"1d": 2, "1m": 1}
    db_session.commit()
    db_session.expire_all()
    got = {(r.interval, r.timestamp): (r.close, r.volume) for r in db_session.query(HistoricData)}
    assert got[("1d", datetime(2026, 4, 20))] == pytest.approx((809.2 / 8, 8000.0))
    assert got[("1d", datetime(2026, 4, 21))] == (101.0, 8000.0)
    assert got[("1m", datetime(2026, 4, 20, 19, 59))] == pytest.approx((809.0 / 8, 80.0))

    assert splits.adjust_history(db_session, "VGT", EX, 8.0) == {}


# ------------------------------------------------------------------
# Books
# ------------------------------------------------------------------


def test_split_multiplies_a_position_held_through_the_ex_date(sqlite_db, db_session):
    _bot(db_session, portfolio={"USD": 100.0, "VGT": 0.2})
    _event(db_session)
    _trade(db_session, datetime(2026, 4, 10, 15), "VGT", 0.2, 790.0, True)

    [r] = splits.apply_splits("B", today=TODAY)
    assert r.qty_at_ex == pytest.approx(0.2)
    assert r.qty_added == pytest.approx(1.4)
    assert _portfolio(db_session) == {"USD": 100.0, "VGT": pytest.approx(1.6)}

    assert splits.apply_splits("B", today=TODAY) == []  # never twice
    assert _portfolio(db_session)["VGT"] == pytest.approx(1.6)


def test_late_application_keeps_what_was_bought_after_the_split(sqlite_db, db_session):
    # The bot held 1 old share, missed the split and "bought the dip": 2 new shares the next day.
    _bot(db_session, portfolio={"USD": 0.0, "VGT": 3.0})
    _event(db_session)
    _trade(db_session, datetime(2026, 4, 16, 15), "VGT", 1.0, 790.0, True)
    _trade(db_session, datetime(2026, 4, 22, 15), "VGT", 2.0, 102.0, True)

    [r] = splits.apply_splits("B", today=TODAY)
    assert r.qty_at_ex == pytest.approx(1.0)
    assert _portfolio(db_session)["VGT"] == pytest.approx(10.0)  # 8 from the split + the 2 bought


def test_late_application_after_selling_out_restores_the_missing_shares(sqlite_db, db_session):
    _bot(db_session, portfolio={"USD": 101.0})
    _event(db_session)
    _trade(db_session, datetime(2026, 4, 22, 15), "VGT", 1.0, 101.0, False)  # sold the 1 share it saw

    splits.apply_splits("B", today=TODAY)
    assert _portfolio(db_session)["VGT"] == pytest.approx(7.0)


def test_ex_date_trades_are_classified_by_price(sqlite_db, db_session):
    _bot(db_session, portfolio={"USD": 0.0, "VGT": 2.0})
    _event(db_session)
    # Before the open at the stale old price: pre-split units.
    _trade(db_session, datetime(2026, 4, 21, 12), "VGT", 1.0, 809.0, True)
    # Before the open at an already-adjusted price: post-split units.
    _trade(db_session, datetime(2026, 4, 21, 12, 30), "VGT", 1.0, 101.0, True)

    [r] = splits.apply_splits("B", today=TODAY)
    assert r.qty_at_ex == pytest.approx(1.0)
    assert _portfolio(db_session)["VGT"] == pytest.approx(9.0)


def test_small_ratio_falls_back_to_the_opening_bell():
    split = Split("X", EX, 1.05, 100.0)
    opened = market_calendar.session_open_utc(EX).replace(tzinfo=None)
    assert opened == datetime(2026, 4, 21, 13, 30)
    assert not splits.traded_after_split(opened - timedelta(minutes=1), 100.0, split)
    assert splits.traded_after_split(opened, 100.0, split)


def test_reverse_split_divides_the_count(sqlite_db, db_session):
    _bot(db_session, portfolio={"USD": 0.0, "SQQQ": 10.0})
    _event(db_session, "SQQQ", EX, 0.2, 10.0)
    splits.apply_splits("B", today=TODAY)
    assert _portfolio(db_session)["SQQQ"] == pytest.approx(2.0)


def test_a_bot_without_the_symbol_is_recorded_and_left_alone(sqlite_db, db_session):
    _bot(db_session, portfolio={"USD": 1000.0, "QQQ": 1.0})
    _event(db_session)
    [r] = splits.apply_splits("B", today=TODAY)
    assert not r.changed
    assert _portfolio(db_session) == {"USD": 1000.0, "QQQ": 1.0}
    row = db_session.query(AppliedSplit).one()
    assert (row.bot_name, row.symbol, row.qty_at_ex, row.qty_added) == ("B", "VGT", 0.0, 0.0)


def test_splits_older_than_the_window_are_not_applied(sqlite_db, db_session):
    _bot(db_session, portfolio={"USD": 0.0, "VGT": 1.0})
    _event(db_session)
    assert splits.apply_splits("B", today=EX + timedelta(days=splits.APPLY_WINDOW_DAYS + 1)) == []
    assert _portfolio(db_session)["VGT"] == 1.0


def test_missing_bot_is_not_created(sqlite_db, db_session):
    _event(db_session)
    assert splits.apply_splits("Nope", today=TODAY) == []
    assert db_session.query(Bot).count() == 0


def test_apply_splits_all_bots(sqlite_db, db_session):
    _bot(db_session, "A", {"USD": 0.0, "VGT": 1.0})
    _bot(db_session, "B", {"USD": 0.0})
    _event(db_session)
    changed, failed = splits.apply_splits_all_bots(today=TODAY)
    assert [r.bot_name for r in changed] == ["A"] and failed == {}
    assert _portfolio(db_session, "A")["VGT"] == pytest.approx(8.0)


# ------------------------------------------------------------------
# Options
# ------------------------------------------------------------------

OLD_CALL = "AAPL261218C00200000"
NEW_CALL = "AAPL261218C00050000"


def test_occ_symbol_round_trips():
    for key in (OLD_CALL, "SPY270115P00612500", "AAPL261218C00068333"):
        assert options.occ_symbol(options.parse_occ(key)) == key


def test_whole_number_split_rekeys_contracts(sqlite_db, db_session):
    _bot(db_session, portfolio={"USD": 0.0, "AAPL": 100.0, OLD_CALL: -100.0})  # covered call
    _event(db_session, "AAPL", EX, 4.0, 800.0)
    _trade(db_session, datetime(2026, 4, 1, 15), OLD_CALL, 100.0, 5.0, False)

    [r] = splits.apply_splits("B", today=TODAY)
    assert r.converted == {OLD_CALL: NEW_CALL}
    assert _portfolio(db_session) == {"USD": 0.0, "AAPL": pytest.approx(400.0), NEW_CALL: pytest.approx(-400.0)}
    assert db_session.query(AppliedSplit).one().note == f"{OLD_CALL} -> {NEW_CALL}"


def test_contracts_opened_after_the_split_are_left_alone(sqlite_db, db_session):
    _bot(db_session, portfolio={"USD": 0.0, NEW_CALL: 100.0})
    _event(db_session, "AAPL", EX, 4.0, 800.0)
    _trade(db_session, datetime(2026, 4, 22, 15), NEW_CALL, 100.0, 1.0, True)
    [r] = splits.apply_splits("B", today=TODAY)
    assert not r.changed
    assert _portfolio(db_session) == {"USD": 0.0, NEW_CALL: 100.0}


def test_non_whole_split_of_a_held_option_fails_loudly_and_changes_nothing(sqlite_db, db_session):
    _bot(db_session, portfolio={"USD": 0.0, OLD_CALL: 100.0})
    _event(db_session, "AAPL", EX, 1.5, 300.0)
    _trade(db_session, datetime(2026, 4, 1, 15), OLD_CALL, 100.0, 5.0, True)
    with pytest.raises(splits.SplitError):
        splits.apply_splits("B", today=TODAY)
    assert _portfolio(db_session) == {"USD": 0.0, OLD_CALL: 100.0}
    assert db_session.query(AppliedSplit).count() == 0


# ------------------------------------------------------------------
# Sweep
# ------------------------------------------------------------------


def test_run_sweep_records_rescales_and_applies(sqlite_db, db_session, monkeypatch):
    _bot(db_session, portfolio={"USD": 0.0, "VGT": 0.5})
    for ts, c in ((datetime(2026, 4, 20), 809.2), (datetime(2026, 4, 21), 101.0)):
        db_session.add(
            HistoricData(symbol="VGT", interval="1d", timestamp=ts, open=c, high=c, low=c, close=c, volume=1)
        )
    db_session.commit()
    monkeypatch.setattr(splits, "fetch_recent_splits", lambda symbols, period: [Split("VGT", EX, 8.0, 809.2)])

    assert splits.sweep_universe() == ["VGT"]
    result = splits.run_sweep(["VGT"], today=TODAY)
    assert [s.symbol for s in result.new] == ["VGT"]
    assert result.history == {("VGT", EX): {"1d": 1}}
    assert [r.bot_name for r in result.applied] == ["B"] and result.failed == {}
    assert _portfolio(db_session)["VGT"] == pytest.approx(4.0)

    again = splits.run_sweep(["VGT"], today=TODAY)
    assert again.new == [] and again.history == {} and again.applied == []


def test_run_sweep_reports_a_yfinance_outage(monkeypatch):
    monkeypatch.setattr(splits, "fetch_recent_splits", lambda symbols, period: None)
    assert splits.run_sweep(["VGT"]) is None


def test_ratio_label():
    assert [splits.ratio_label(r) for r in (8.0, 0.2, 1.241, 2.0)] == ["8:1", "1:5", "1.241:1", "2:1"]
