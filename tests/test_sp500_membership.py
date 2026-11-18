from datetime import date

import pandas as pd

from tradingbot.utils.universes import (
    SP500,
    SP500_AS_OF,
    SP500_EXCLUDED,
    SP500_SECTOR,
    sp500_members,
)


def test_sp500_constants() -> None:
    assert isinstance(SP500, tuple)
    assert len(SP500) == 498
    assert isinstance(SP500_AS_OF, str)
    assert len(SP500_EXCLUDED) == 5
    assert "GOOG" in SP500_EXCLUDED
    assert "BRK.B" in SP500_EXCLUDED

    # All traded names must be in SP500_SECTOR
    for symbol in SP500:
        assert symbol in SP500_SECTOR
        assert isinstance(SP500_SECTOR[symbol], str)
        assert len(SP500_SECTOR[symbol]) > 0


def test_sp500_membership_point_in_time() -> None:
    # TSLA was added to S&P 500 on 2020-12-21; PAYC on 2020-01-28.
    # Neither should be present in the 2019-06-30 point-in-time universe.
    members_2019 = sp500_members("2019-06-30")
    assert isinstance(members_2019, tuple)
    assert "TSLA" not in members_2019
    assert "PAYC" not in members_2019
    assert "TSLA" in SP500

    # ALTR (Altera) was removed 2015-12-29; KRFT was removed 2015-07-06.
    # Both should be present in 2014 point-in-time membership, but not today.
    members_2014_str = sp500_members("2014")
    members_2014_date = sp500_members("2014-12-31")
    assert "ALTR" in members_2014_str
    assert "ALTR" in members_2014_date
    assert "KRFT" in members_2014_str
    assert "KRFT" in members_2014_date
    assert "ALTR" not in SP500
    assert "KRFT" not in SP500


def test_sp500_membership_date_types() -> None:
    # Supports string, datetime.date, and pd.Timestamp
    m_str = sp500_members("2019-06-30")
    m_date = sp500_members(date(2019, 6, 30))
    m_ts = sp500_members(pd.Timestamp("2019-06-30"))

    assert m_str == m_date
    assert m_str == m_ts
    assert "TSLA" not in m_date


def test_sp500_members_as_of_latest() -> None:
    # Calling as_of today or after SP500_AS_OF should return the current SP500 universe
    current = sp500_members("2026-11-18")
    assert current == SP500
