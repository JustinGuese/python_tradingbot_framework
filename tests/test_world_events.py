"""World-event latency logger: parsing, dedupe, idempotent storage, snapshot scheduling."""

from datetime import datetime, timedelta

from tradingbot.utils import world_events as we
from tradingbot.utils.db import EventPriceSnapshot, WorldEvent

RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Fed holds rates</title><link>https://x/1</link><guid>g1</guid>
<pubDate>Wed, 05 Nov 2026 18:00:00 GMT</pubDate><description>d</description></item>
<item><description>no title</description></item></channel></rss>"""
ATOM = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>M6.1 quake</title>
<id>urn:q1</id><link href="https://u/q1"/><updated>2026-11-05T18:00:05Z</updated></entry></feed>"""


def test_parse_rss_and_atom():
    (r,) = we.parse_feed("fed", RSS)
    assert (r.source, r.external_id, r.url) == ("rss:fed", "g1", "https://x/1")
    assert r.source_ts == datetime(2026, 11, 5, 18, 0)
    (a,) = we.parse_feed("usgs", ATOM)
    assert a.external_id == "urn:q1" and a.url == "https://u/q1"
    assert a.source_ts == datetime(2026, 11, 5, 18, 0, 5)


def test_parse_alpaca():
    (e,) = we.parse_alpaca_news(
        [{"id": 7, "headline": "Oil jumps", "symbols": ["USO", "XLE"], "created_at": "2026-11-05T18:00:00Z"}, {"id": 8}]
    )
    assert e.symbols == "USO,XLE" and e.source_ts == datetime(2026, 11, 5, 18, 0)


def test_dedupe_key_ignores_case_and_punctuation():
    assert we.dedupe_key("Oil JUMPS, 5%!") == we.dedupe_key("oil jumps 5")
    assert we.dedupe_key("a") != we.dedupe_key("b")


def test_due_offsets_window_and_done():
    rec = datetime(2026, 11, 5, 18, 0)
    assert we.due_offsets(rec, rec + timedelta(seconds=5), set()) == [0]
    assert we.due_offsets(rec, rec + timedelta(seconds=61), {0}) == [60]
    # stale: 10 minutes late for every offset <= 300 -> only the future-due ones are skipped too
    assert we.due_offsets(rec, rec + timedelta(seconds=300 + we.SNAPSHOT_GRACE_S + 1), {0, 60}) == []
    assert we.due_offsets(rec, rec - timedelta(seconds=1), set()) == []


def test_store_events_idempotent_and_is_first(sqlite_db, db_session):
    t = datetime(2026, 11, 5, 18, 0, 10)
    a = we.RawEvent("rss:bbc", "1", "Oil jumps", source_ts=t - timedelta(seconds=30), received_at=t)
    b = we.RawEvent("alpaca", "9", "OIL jumps!", received_at=t + timedelta(seconds=20))
    assert len(we.store_events([a, b])) == 2
    assert we.store_events([a]) == []  # same source+external_id
    rows = {r.source: r for r in db_session.query(WorldEvent).all()}
    assert rows["rss:bbc"].is_first is True and rows["alpaca"].is_first is False
    assert rows["rss:bbc"].dedupe_key == rows["alpaca"].dedupe_key


def test_snapshot_pass_only_first_sightings_and_idempotent(sqlite_db, db_session, monkeypatch):
    now = datetime(2026, 11, 5, 18, 0, 30)
    we.store_events(
        [
            we.RawEvent("rss:bbc", "1", "Oil jumps", received_at=now - timedelta(seconds=30)),
            we.RawEvent("alpaca", "9", "Oil jumps", received_at=now - timedelta(seconds=20)),
        ]
    )
    bar = datetime(2026, 11, 5, 18, 0)
    monkeypatch.setattr(we, "latest_prices", lambda syms: {"SPY": (500.0, bar)})
    assert we.run_snapshot_pass(["SPY", "QQQ"], now=now) == 2  # offset 0 only, SPY + QQQ, one event
    assert we.run_snapshot_pass(["SPY", "QQQ"], now=now) == 0  # already stored
    snaps = db_session.query(EventPriceSnapshot).order_by(EventPriceSnapshot.symbol).all()
    assert [(s.symbol, s.price, s.offset_s) for s in snaps] == [("QQQ", None, 0), ("SPY", 500.0, 0)]
    assert len({s.event_id for s in snaps}) == 1
