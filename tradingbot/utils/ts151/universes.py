"""Named universes and resolution for TS151 strategy bots (PLAN-ts151.md S0)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tradingbot.utils.universes import SP100, SP500, SP500_SECTOR

# Core ETF / Index universes
SECTORS: tuple[str, ...] = ("XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY")
MULTI_ASSET: tuple[str, ...] = ("SPY", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC", "VNQ")

# Treasury curve ETFs and parameters
TREASURY_CURVE: tuple[str, ...] = ("SHY", "IEI", "IEF", "TLH", "TLT")
TREASURY_MATURITY: dict[str, float] = {
    "SHY": 2.0,
    "IEI": 5.0,
    "IEF": 8.5,
    "TLH": 15.0,
    "TLT": 25.0,
}
TREASURY_DURATION: dict[str, float] = {
    "SHY": 1.9,
    "IEI": 4.4,
    "IEF": 7.5,
    "TLH": 11.5,
    "TLT": 17.0,
}
# FRED series per Treasury curve ETF; IEF is matched to the average of DGS7 and DGS10
TREASURY_FRED: dict[str, str | tuple[str, str]] = {
    "SHY": "DGS2",
    "IEI": "DGS5",
    "IEF": ("DGS7", "DGS10"),
    "TLH": "DGS20",
    "TLT": "DGS30",
}

# Fixed income universes
BOND_ETFS: tuple[str, ...] = (
    "SHY",
    "IEI",
    "IEF",
    "TLH",
    "TLT",
    "TIP",
    "LQD",
    "HYG",
    "MUB",
    "MBB",
    "EMB",
    "BWX",
)
COUNTRY_BONDS: tuple[str, ...] = ("IEF", "BWX", "IGOV", "EMB")

# FX ETFs
FX_ETFS: tuple[str, ...] = ("FXE", "FXB", "FXA", "FXC", "FXF", "FXY")
FX_ETF_TO_CCY: dict[str, str] = {
    "FXE": "EUR",
    "FXB": "GBP",
    "FXA": "AUD",
    "FXC": "CAD",
    "FXF": "CHF",
    "FXY": "JPY",
}

# Commodities & CFTC COT codes
COMMODITIES: tuple[str, ...] = (
    "USO",
    "UNG",
    "GLD",
    "SLV",
    "CPER",
    "DBA",
    "CORN",
    "WEAT",
    "SOYB",
    "CANE",
)
COT_CODES: dict[str, str] = {
    "USO": "067651",
    "UNG": "023651",
    "GLD": "088691",
    "SLV": "084691",
    "CPER": "085692",
    "CORN": "002602",
    "WEAT": "001602",
    "SOYB": "005602",
    "CANE": "080732",
}

# Trend, Futures, Leveraged
TREND_ETFS: tuple[str, ...] = (
    "SPY",
    "QQQ",
    "EFA",
    "EEM",
    "IEF",
    "TLT",
    "GLD",
    "USO",
    "DBA",
    "UUP",
    "FXE",
    "FXY",
)
FUTURES: tuple[str, ...] = (
    "ES=F",
    "NQ=F",
    "ZN=F",
    "ZB=F",
    "GC=F",
    "SI=F",
    "HG=F",
    "CL=F",
    "6E=F",
    "6J=F",
)

LEVERAGED_PAIRS: tuple[tuple[str, str], ...] = (
    ("TQQQ", "SQQQ"),
    ("SPXL", "SPXS"),
)
LEVERAGED_ETFS: tuple[str, ...] = ("TQQQ", "SQQQ", "SPXL", "SPXS")

# Real Estate, Inflation Hedge, Crypto
REITS: tuple[str, ...] = ("VNQ", "IYR", "RWR", "ICF", "REM", "REZ", "VNQI")
INFLATION_HEDGE: tuple[str, ...] = ("TIP", "GLD", "DBC", "VNQ", "XLE")
CRYPTO: tuple[str, ...] = ("BTC-USD", "ETH-USD")

_NAMED_UNIVERSES: dict[str, tuple[str, ...]] = {
    "sp500": SP500,
    "sp100": SP100,
    "sectors": SECTORS,
    "multi_asset": MULTI_ASSET,
    "treasury_curve": TREASURY_CURVE,
    "bond_etfs": BOND_ETFS,
    "country_bonds": COUNTRY_BONDS,
    "fx_etfs": FX_ETFS,
    "commodities": COMMODITIES,
    "trend_etfs": TREND_ETFS,
    "futures": FUTURES,
    "leveraged_pairs": LEVERAGED_ETFS,
    "reits": REITS,
    "inflation_hedge": INFLATION_HEDGE,
    "crypto": CRYPTO,
}


def resolve(name_or_seq: str | Sequence[str]) -> tuple[str, ...]:
    """Resolve a universe identifier (name string or ticker sequence) to a tuple of tickers.

    Raises ValueError for unknown names or invalid input types.
    """
    if isinstance(name_or_seq, str):
        key = name_or_seq.strip().lower()
        if key in _NAMED_UNIVERSES:
            return _NAMED_UNIVERSES[key]
        raise ValueError(
            f"Unknown universe name {name_or_seq!r}; known names are: "
            f"{', '.join(sorted(_NAMED_UNIVERSES.keys()))}"
        )

    if isinstance(name_or_seq, (tuple, list, set)):
        return tuple(str(s) for s in name_or_seq)

    raise ValueError(f"Invalid universe specification: {name_or_seq!r}")
