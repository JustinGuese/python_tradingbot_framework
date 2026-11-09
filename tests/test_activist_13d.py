"""Tests for the 13D rules, scanner and Activist13DBot."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import scripts.onetime_event_13d_study as study
import tradingbot.utils.sc13d_scan as scan_mod
from tradingbot.activist13dbot import PLACEHOLDER, Activist13DBot, read_sc13d_events
from tradingbot.utils import db as dbmod
from tradingbot.utils.botclass import Bot
from tradingbot.utils.db import Sc13dEvent
from tradingbot.utils.market_calendar import next_session
from tradingbot.utils.sc13d_rules import FUND_RE, NAME_RE, is_fund_like, is_initial_13d, reporting_names
from tradingbot.utils.universes import ACTIVIST_UNIVERSE, SP100

COVER = (
    "SCHEDULE 13D CUSIP No. 123 1 NAMES OF REPORTING PERSONS Starboard Value LP "
    "I.R.S. IDENTIFICATION NOS. OF ABOVE PERSONS 2 CHECK THE APPROPRIATE BOX"
)


# ---- rules ----------------------------------------------------------------------------------------------------
def test_classifier_is_identical_to_the_study_that_evidenced_it():
    assert FUND_RE.pattern == study.FUND_RE.pattern and FUND_RE.flags == study.FUND_RE.flags
    assert NAME_RE.pattern == study.NAME_RE.pattern and NAME_RE.flags == study.NAME_RE.flags


def test_names_and_fund_classification():
    assert reporting_names(COVER) == "Starboard Value LP"
    assert is_fund_like("Starboard Value LP")
    assert not is_fund_like("John Q. Smith")
    assert not is_fund_like(None)  # an unparsed cover is never fund-like
    assert reporting_names("no cover here") is None


def test_cover_page_far_into_the_document_is_still_parsed():
    # The post-2024 SCHEDULE 13D format puts the cover ~7,000 characters in behind an SEC preamble; a 6,000
    # character window found no filer in any filing of the first live smoke test.
    text = (
        "preamble " * 900 + "1 Name of reporting person HoldCo Opportunities Fund V, L.P. 2 Check the appropriate box"
    )
    assert len(text) > 7000 and reporting_names(text[:6000]) is None
    assert reporting_names(text[: scan_mod.COVER_SEARCH_CHARS]) == "HoldCo Opportunities Fund V, L.P."
    assert is_fund_like("HoldCo Opportunities Fund V, L.P.") and not is_fund_like("SK hynix Inc.")


def test_only_initial_13d_forms_count():
    assert is_initial_13d("SC 13D") and is_initial_13d("SCHEDULE 13D")
    assert not is_initial_13d("SC 13D/A") and not is_initial_13d("SCHEDULE 13D/A") and not is_initial_13d("SC 13G")


def test_universe_is_frozen_and_contains_sp100():
    assert len(ACTIVIST_UNIVERSE) == len(set(ACTIVIST_UNIVERSE)) > 1000
    assert set(SP100) <= set(ACTIVIST_UNIVERSE)


# ---- scanner --------------------------------------------------------------------------------------------------
def _subs(forms, dates, accs):
    return {
        "filings": {
            "recent": {
                "form": forms,
                "filingDate": dates,
                "accessionNumber": accs,
                "acceptanceDateTime": [f"{d}T21:30:00.000Z" for d in dates],
                "primaryDocument": ["doc.htm"] * len(forms),
            }
        }
    }


def test_parse_keeps_only_recent_initial_13d():
    data = _subs(
        ["SC 13D", "SC 13D/A", "SC 13G", "SCHEDULE 13D", "SC 13D"],
        ["2026-11-02", "2026-11-02", "2026-11-02", "2026-11-03", "2026-01-05"],
        ["a1", "a2", "a3", "a4", "a5"],
    )
    got = scan_mod.parse_13d_filings(data, "XYZ", 1, datetime(2026, 10, 1))
    assert [g["accession"] for g in got] == ["a1", "a4"]


@pytest.fixture
def scratch_db(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    dbmod.Base.metadata.create_all(eng)
    maker = sessionmaker(bind=eng, autocommit=False, autoflush=False)

    @contextmanager
    def _session():
        s = maker()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    for mod in ("tradingbot.utils.sc13d_scan", "tradingbot.activist13dbot"):
        monkeypatch.setattr(f"{mod}.get_db_session", _session)
    return _session


def test_scan_stores_classified_events_once_and_leaves_failed_docs_for_the_next_run(scratch_db, monkeypatch):
    now = datetime(2026, 11, 6, 22, 0)
    subs = _subs(["SC 13D", "SC 13D"], ["2026-11-05", "2026-11-05"], ["fund-1", "bad-doc"])
    broken = {"on": True}

    def fake_fetch(url, rate_limiter=None, **kw):
        if "submissions" in url:
            return json.dumps(subs).encode()
        if broken["on"] and "/baddoc/" in url:
            raise RuntimeError("boom")
        return f"<p>{COVER}</p>".encode()

    monkeypatch.setattr(scan_mod, "fetch_edgar_bytes", fake_fetch)
    monkeypatch.setattr(scan_mod, "load_sec_ticker_to_cik", lambda rate_limiter=None: {"XYZ": 99})

    first = scan_mod.scan(["XYZ"], since_days=30, now=now)
    assert [r.accession for r in first] == ["fund-1"]  # the unfetchable doc is NOT stored unclassified
    assert scan_mod.scan(["XYZ"], since_days=30, now=now) == []  # fund-1 is not inserted twice

    broken["on"] = False  # the retry picks the failed filing up
    assert [r.accession for r in scan_mod.scan(["XYZ"], since_days=30, now=now)] == ["bad-doc"]
    with scratch_db() as s:
        rows = s.query(Sc13dEvent).all()
        assert len(rows) == 2 and all(r.fund_like for r in rows)
        assert {r.filers for r in rows} == {"Starboard Value LP"}


def test_scan_raises_when_too_many_symbols_fail(scratch_db, monkeypatch):
    monkeypatch.setattr(scan_mod, "load_sec_ticker_to_cik", lambda rate_limiter=None: {})
    with pytest.raises(RuntimeError, match="13D scan failed"):
        scan_mod.scan(["AAA", "BBB"], since_days=5, now=datetime(2026, 11, 6))


def test_heartbeat_is_refreshed_not_duplicated(scratch_db):
    scan_mod.touch_heartbeat(datetime(2026, 11, 1, tzinfo=UTC))
    scan_mod.touch_heartbeat(datetime(2026, 11, 5, tzinfo=UTC))
    with scratch_db() as s:
        rows = s.query(Sc13dEvent).filter_by(accession="heartbeat").all()
        assert len(rows) == 1 and rows[0].created_at == datetime(2026, 11, 5)


# ---- reader and bot -------------------------------------------------------------------------------------------
def test_reader_fails_loudly_on_empty_or_stale_table(scratch_db):
    with pytest.raises(RuntimeError, match="empty"):
        read_sc13d_events(now=datetime(2026, 11, 6, tzinfo=UTC))
    scan_mod.touch_heartbeat(datetime(2026, 10, 1, tzinfo=UTC))
    with pytest.raises(RuntimeError, match="stale"):
        read_sc13d_events(now=datetime(2026, 11, 6, tzinfo=UTC))


def test_reader_returns_only_fund_like_events_inside_the_window(scratch_db):
    scan_mod.touch_heartbeat(datetime(2026, 11, 5, tzinfo=UTC))
    with scratch_db() as s:
        s.add_all(
            [
                Sc13dEvent(symbol="AAA", accession="1", filed_at=datetime(2026, 11, 2, 21), fund_like=True),
                Sc13dEvent(symbol="BBB", accession="2", filed_at=datetime(2026, 11, 2, 21), fund_like=False),
                Sc13dEvent(symbol="CCC", accession="3", filed_at=datetime(2026, 8, 1, 21), fund_like=True),
            ]
        )
    got = read_sc13d_events(now=datetime(2026, 11, 6, tzinfo=UTC))
    assert [e["symbol"] for e in got] == ["AAA"]


@pytest.fixture
def make_bot(mocker):
    mocker.patch.object(Bot, "__init__", lambda self, *a, **k: None)

    def _factory(events):
        bot = Activist13DBot(events=events)
        bot.bot_name = "Activist13DBot"
        return bot

    return _factory


def _rows(day: date) -> dict[str, pd.Series]:
    return {PLACEHOLDER: pd.Series({"timestamp": pd.Timestamp(day)})}


def _ev(sym, filed):
    return {"symbol": sym, "filed_at": filed}


def test_no_events_means_cash(make_bot):
    assert make_bot([]).targetWeights(_rows(date(2026, 11, 6))) == {}


def test_one_event_is_capped_at_half_and_entered_at_the_next_close(make_bot):
    bot = make_bot([_ev("AAA", datetime(2026, 11, 3, 22, 0, tzinfo=UTC))])  # after the 3 Nov close
    assert bot.targetWeights(_rows(date(2026, 11, 3))) == {}  # not held on the filing day
    assert bot.targetWeights(_rows(date(2026, 11, 4))) == {"AAA": 0.5}


def test_three_active_names_share_equally(make_bot):
    f = datetime(2026, 11, 3, 22, 0, tzinfo=UTC)
    w = make_bot([_ev("AAA", f), _ev("BBB", f), _ev("CCC", f)]).targetWeights(_rows(date(2026, 11, 4)))
    assert w == {s: pytest.approx(1 / 3) for s in ("AAA", "BBB", "CCC")}


def test_position_exits_after_twenty_sessions(make_bot):
    bot = make_bot([_ev("AAA", datetime(2026, 11, 3, 22, 0, tzinfo=UTC))])
    entry = date(2026, 11, 4)
    day = entry
    for _ in range(19):
        day = next_session(day)
    assert bot.targetWeights(_rows(day)) == {"AAA": 0.5}  # 20th held session
    assert bot.targetWeights(_rows(next_session(day))) == {}  # exited


def test_repeat_filings_within_thirty_days_do_not_restart_the_clock(make_bot):
    first = datetime(2026, 11, 3, 22, 0, tzinfo=UTC)
    bot = make_bot([_ev("AAA", first), _ev("AAA", first + timedelta(days=14))])
    day = date(2026, 11, 4)
    for _ in range(20):
        day = next_session(day)  # 20 sessions after entry: out, although the 2nd filing is only ~6 sessions old
    assert bot.targetWeights(_rows(day)) == {}


def test_ticker_list_is_event_names_plus_a_never_weighted_placeholder(make_bot):
    bot = make_bot(
        [_ev("AAA", datetime(2026, 11, 3, 22, tzinfo=UTC)), _ev("BBB", datetime(2026, 11, 3, 22, tzinfo=UTC))]
    )
    assert Activist13DBot.LIQUIDATE_UNTRACKED is True
    assert Activist13DBot.MIN_UNIVERSE_COVERAGE == 0.5
    assert PLACEHOLDER not in bot.targetWeights(_rows(date(2026, 11, 4)))
