"""Sector ETF mapping for S&P 100 constituent equities.

Maps every symbol in the S&P 100 universe (tradingbot.utils.universes.SP100) to
its corresponding sector ETF from the original nine Select Sector SPDR ETFs
(tradingbot.utils.quantsmd_common.SECTORS9):
    XLB: Materials
    XLE: Energy
    XLF: Financials (including Real Estate approximation)
    XLI: Industrials
    XLK: Technology (including Internet & Telecom communication names approximation)
    XLP: Consumer Staples
    XLU: Utilities
    XLV: Health Care
    XLY: Consumer Discretionary (including Media & Entertainment communication names approximation)

Approximation notes:
--------------------
1. Communication Services (GICS Sector 50):
   In September 2018, S&P and MSCI carved out Communication Services (XLC) from
   Telecommunication Services (formerly in XLK/VOX), Information Technology (GOOGL, META),
   and Consumer Discretionary (CMCSA, DIS, NFLX).
   Because SECTORS9 models the nine original 1998 sector ETFs that predate XLC,
   communication names are mapped to their closest historical sector ETF:
     - Internet & Telecom names (GOOGL, META, T, TMUS, VZ) -> XLK (Technology)
     - Media & Entertainment names (CMCSA, DIS, NFLX) -> XLY (Consumer Discretionary)

2. Real Estate (GICS Sector 60):
   Prior to September 2016, Real Estate was classified under Financials (GICS Sector 40).
   Real Estate constituents in SP100 (such as AMT) map to XLF (Financials), preserving
   compatibility with the 9-sector SPDR universe.
"""

from __future__ import annotations

from tradingbot.utils.quantsmd_common import SECTORS9

# Mapping from every SP100 constituent to its assigned SECTORS9 sector ETF.
SECTOR_ETF: dict[str, str] = {
    # Materials (XLB)
    "LIN": "XLB",
    # Energy (XLE)
    "COP": "XLE",
    "CVX": "XLE",
    "XOM": "XLE",
    # Financials (XLF) - including Real Estate (AMT)
    "AMT": "XLF",
    "AXP": "XLF",
    "BAC": "XLF",
    "BLK": "XLF",
    "BNY": "XLF",
    "C": "XLF",
    "COF": "XLF",
    "GS": "XLF",
    "JPM": "XLF",
    "MA": "XLF",
    "MS": "XLF",
    "SCHW": "XLF",
    "USB": "XLF",
    "V": "XLF",
    "WFC": "XLF",
    # Industrials (XLI)
    "BA": "XLI",
    "CAT": "XLI",
    "DE": "XLI",
    "EMR": "XLI",
    "FDX": "XLI",
    "GD": "XLI",
    "GE": "XLI",
    "GEV": "XLI",
    "LMT": "XLI",
    "MMM": "XLI",
    "RTX": "XLI",
    "UBER": "XLI",
    "UNP": "XLI",
    "UPS": "XLI",
    # Information Technology (XLK) + Tech/Telecom Communication names
    "AAPL": "XLK",
    "ACN": "XLK",
    "ADBE": "XLK",
    "AMAT": "XLK",
    "AMD": "XLK",
    "ANET": "XLK",
    "AVGO": "XLK",
    "CRM": "XLK",
    "CSCO": "XLK",
    "DELL": "XLK",
    "GOOGL": "XLK",
    "IBM": "XLK",
    "INTC": "XLK",
    "INTU": "XLK",
    "LRCX": "XLK",
    "META": "XLK",
    "MSFT": "XLK",
    "MU": "XLK",
    "NOW": "XLK",
    "NVDA": "XLK",
    "ORCL": "XLK",
    "PANW": "XLK",
    "PLTR": "XLK",
    "QCOM": "XLK",
    "SNDK": "XLK",
    "T": "XLK",
    "TMUS": "XLK",
    "TXN": "XLK",
    "VZ": "XLK",
    # Consumer Staples (XLP)
    "COST": "XLP",
    "KO": "XLP",
    "MDLZ": "XLP",
    "MO": "XLP",
    "PEP": "XLP",
    "PG": "XLP",
    "PM": "XLP",
    "WMT": "XLP",
    # Utilities (XLU)
    "DUK": "XLU",
    "NEE": "XLU",
    "SO": "XLU",
    # Health Care (XLV)
    "ABBV": "XLV",
    "ABT": "XLV",
    "AMGN": "XLV",
    "BMY": "XLV",
    "CVS": "XLV",
    "DHR": "XLV",
    "GILD": "XLV",
    "ISRG": "XLV",
    "JNJ": "XLV",
    "LLY": "XLV",
    "MDT": "XLV",
    "MRK": "XLV",
    "PFE": "XLV",
    "TMO": "XLV",
    "UNH": "XLV",
    # Consumer Discretionary (XLY) + Media/Entertainment Communication names
    "AMZN": "XLY",
    "BKNG": "XLY",
    "CMCSA": "XLY",
    "DIS": "XLY",
    "GM": "XLY",
    "HD": "XLY",
    "LOW": "XLY",
    "MCD": "XLY",
    "NFLX": "XLY",
    "SBUX": "XLY",
    "TSLA": "XLY",
}

# Known aliases and excluded ticker variations (e.g. from SP100_EXCLUDED).
_ALIASES: dict[str, str] = {
    "GOOG": "XLK",
    "BRK.B": "XLF",
    "BRK-B": "XLF",
}


def sector_etf(symbol: str) -> str:
    """Return the corresponding SECTORS9 ETF symbol for an equity symbol.

    Parameters
    ----------
    symbol : str
        Stock ticker symbol (e.g. from SP100) or an existing SECTORS9 ETF symbol.

    Returns
    -------
    str
        One of the nine SECTORS9 ETFs ('XLB', 'XLE', 'XLF', 'XLI', 'XLK',
        'XLP', 'XLU', 'XLV', 'XLY').

    Raises
    ------
    KeyError
        If the symbol cannot be mapped to any of the SECTORS9 ETFs.
    """
    cleaned = symbol.strip().upper()
    if cleaned in SECTOR_ETF:
        return SECTOR_ETF[cleaned]
    if cleaned in _ALIASES:
        return _ALIASES[cleaned]
    if cleaned in SECTORS9:
        return cleaned
    raise KeyError(f"No SECTORS9 sector ETF mapped for symbol: {symbol!r}")


__all__ = ["SECTORS9", "SECTOR_ETF", "sector_etf"]
