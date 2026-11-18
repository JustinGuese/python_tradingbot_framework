import pytest

from tradingbot.utils.quantsmd_common import SECTORS9
from tradingbot.utils.sector_map import SECTOR_ETF, sector_etf
from tradingbot.utils.universes import SP100


def test_sector_etf_completeness():
    """Verify that every SP100 constituent is mapped to one of SECTORS9."""
    assert len(SECTOR_ETF) == len(SP100)
    assert set(SECTOR_ETF.keys()) == set(SP100)
    for sym, etf in SECTOR_ETF.items():
        assert etf in SECTORS9, f"{sym} mapped to invalid ETF {etf}"


def test_all_sectors_represented():
    """Ensure all 9 sector ETFs have at least one SP100 constituent."""
    mapped_etfs = set(SECTOR_ETF.values())
    assert mapped_etfs == set(SECTORS9)


def test_communication_names_approximation():
    """Verify communication names map to either XLK or XLY as specified."""
    # Tech / Telecom communication names
    for sym in ["GOOGL", "META", "T", "TMUS", "VZ"]:
        assert SECTOR_ETF[sym] == "XLK", f"{sym} should map to XLK"

    # Media / Entertainment communication names
    for sym in ["CMCSA", "DIS", "NFLX"]:
        assert SECTOR_ETF[sym] == "XLY", f"{sym} should map to XLY"


def test_real_estate_approximation():
    """Verify Real Estate constituent AMT maps to XLF (historical pre-2016 home)."""
    assert SECTOR_ETF["AMT"] == "XLF"


def test_sector_etf_lookup():
    """Verify sector_etf function behavior with various inputs."""
    # Normal SP100 symbols
    assert sector_etf("AAPL") == "XLK"
    assert sector_etf("JPM") == "XLF"
    assert sector_etf("XOM") == "XLE"
    assert sector_etf("LIN") == "XLB"
    assert sector_etf("CAT") == "XLI"
    assert sector_etf("PG") == "XLP"
    assert sector_etf("NEE") == "XLU"
    assert sector_etf("JNJ") == "XLV"
    assert sector_etf("AMZN") == "XLY"

    # Case insensitivity and whitespace handling
    assert sector_etf("aapl") == "XLK"
    assert sector_etf("  msft  ") == "XLK"

    # Known aliases / excluded share classes
    assert sector_etf("GOOG") == "XLK"
    assert sector_etf("BRK.B") == "XLF"
    assert sector_etf("BRK-B") == "XLF"

    # Sector ETFs themselves
    for etf in SECTORS9:
        assert sector_etf(etf) == etf

    # Unknown symbols raise KeyError
    with pytest.raises(KeyError):
        sector_etf("NONEXISTENT_TICKER")
