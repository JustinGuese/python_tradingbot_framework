"""
Named stock universes.

Hard-coded on purpose. Fetching constituents at runtime would make every run
depend on a scraped HTML page and let the universe change silently between a
backtest and the live run it justified. Refresh with
scripts/onetime_refresh_sp100.py and commit the diff.

Using TODAY's constituents over a multi-year backtest carries survivorship
bias: these are the companies that grew into the index. Compare any strategy
on this universe against an equal-weight hold of the same list, not only
against QQQ (docs/backtests/pead-2026-09.md measured that bias alone at about
+4.7%/yr on a similar large-cap list).
"""

SP100_AS_OF = "2026-09"

SP100: tuple[str, ...] = (
    "AAPL",
    "ABBV",
    "ABT",
    "ACN",
    "ADBE",
    "AMAT",
    "AMD",
    "AMGN",
    "AMT",
    "AMZN",
    "ANET",
    "AVGO",
    "AXP",
    "BA",
    "BAC",
    "BKNG",
    "BLK",
    "BMY",
    "BNY",
    "C",
    "CAT",
    "CMCSA",
    "COF",
    "COP",
    "COST",
    "CRM",
    "CSCO",
    "CVS",
    "CVX",
    "DE",
    "DELL",
    "DHR",
    "DIS",
    "DUK",
    "EMR",
    "FDX",
    "GD",
    "GE",
    "GEV",
    "GILD",
    "GM",
    "GOOGL",
    "GS",
    "HD",
    "IBM",
    "INTC",
    "INTU",
    "ISRG",
    "JNJ",
    "JPM",
    "KO",
    "LIN",
    "LLY",
    "LMT",
    "LOW",
    "LRCX",
    "MA",
    "MCD",
    "MDLZ",
    "MDT",
    "META",
    "MMM",
    "MO",
    "MRK",
    "MS",
    "MSFT",
    "MU",
    "NEE",
    "NFLX",
    "NOW",
    "NVDA",
    "ORCL",
    "PANW",
    "PEP",
    "PFE",
    "PG",
    "PLTR",
    "PM",
    "QCOM",
    "RTX",
    "SBUX",
    "SCHW",
    "SNDK",
    "SO",
    "T",
    "TMO",
    "TMUS",
    "TSLA",
    "TXN",
    "UBER",
    "UNH",
    "UNP",
    "UPS",
    "USB",
    "V",
    "VZ",
    "WFC",
    "WMT",
    "XOM",
)

# Index members this framework does not trade, with the reason.
SP100_EXCLUDED: dict[str, str] = {
    # Share-class duplicate of GOOGL; holding both double-weights one company.
    "GOOG": "duplicate share class of GOOGL",
    # yfinance spells it BRK-B, Collective2 BRK.B; neither symbol survives the
    # copier's no-"-"/"." symbol rule, so the live book could not mirror it.
    "BRK.B": "class-B ticker not tradeable through the copier's symbol rules",
}
