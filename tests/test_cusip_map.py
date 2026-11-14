"""CUSIP map tests; the HTTP layer is mocked, no network."""

from tradingbot.utils.cusip_map import coverage, resolve_cusips
from tradingbot.utils.db import CusipMap


def _fake_post(calls):
    def post(jobs):
        calls.append([j["idValue"] for j in jobs])
        assert all(set(j) == {"idType", "idValue"} for j in jobs)
        return [
            {"data": [{"ticker": "T" + j["idValue"][:3], "exchCode": "US", "securityType": "Common Stock"}]}
            if not j["idValue"].startswith("BAD")
            else {"warning": "No identifier found."}
            for j in jobs
        ]

    return post


def test_batching_and_cache_hit(db_session, monkeypatch):
    monkeypatch.delenv("OPENFIGI_API_KEY", raising=False)
    calls: list[list[str]] = []
    cusips = [f"{i:03d}000000" for i in range(25)]
    out = resolve_cusips(db_session, cusips, post=_fake_post(calls), sleep=lambda s: None)
    assert [len(c) for c in calls] == [10, 10, 5]
    assert out["003000000"] == "T003"
    resolve_cusips(db_session, cusips, post=_fake_post(calls), sleep=lambda s: None)
    assert len(calls) == 3  # no second call
    assert coverage(db_session, [(c, 1.0) for c in cusips])["coverage"] == 1.0


def test_unresolved_stored_null_not_requeried(db_session, monkeypatch):
    monkeypatch.delenv("OPENFIGI_API_KEY", raising=False)
    calls: list[list[str]] = []
    out = resolve_cusips(db_session, ["BAD000001", "111000000"], post=_fake_post(calls), sleep=lambda s: None)
    assert out["BAD000001"] is None
    row = db_session.get(CusipMap, "BAD000001")
    assert row is not None and row.ticker is None
    resolve_cusips(db_session, ["BAD000001"], post=_fake_post(calls), sleep=lambda s: None)
    assert len(calls) == 1
    cov = coverage(db_session, [("BAD000001", 3.0), ("111000000", 1.0)])
    assert cov["coverage"] == 0.25 and cov["unresolved"] == [("BAD000001", 3.0)]
