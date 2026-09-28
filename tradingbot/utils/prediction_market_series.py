"""
The hand-curated list of prediction-market series we capture.

Curated on purpose, never auto-discovered: two markets with the same headline
can resolve on different rules, and a feature that silently mixes them measures
nothing. Kalshi's own recession series is the example — "Will there be a
recession in 2026?" resolves Yes on two consecutive negative GDP quarters
anywhere in 2025 *or* 2026 (a rolling two-year window, not calendar 2026, and
not NBER despite the RECSSNBER ticker). Read a series' rules before adding it.

Each series names the assets it should move and the expected sign, which is
documentation for whoever builds on it, not something code enforces.

Index/crypto threshold series (Kalshi KXINXU, KXBTCD) are deliberately absent:
their markets live ~24 hours and need hourly prices, so the calibration study
(scripts/onetime_prediction_market_calibration.py) reads them directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Series:
    key: str
    venue: str  # kalshi / polymarket
    sources: tuple[str, ...]  # Kalshi series tickers, or Polymarket event slugs
    description: str
    assets: dict[str, int] = field(default_factory=dict)  # asset -> expected sign of a rising prob


SERIES: tuple[Series, ...] = (
    Series(
        "fed_rate",
        "kalshi",
        ("KXFED",),
        "Upper bound of the fed funds target after each FOMC meeting, 'above X%' ladders (2021-07+; "
        "a single 0.25% liftoff strike until 2021-12).",
        {"TLT": -1, "IEF": -1, "QQQ": -1},
    ),
    Series(
        "fed_decision",
        "kalshi",
        ("KXFEDDECISION",),
        "Outcome of each FOMC meeting: H0 hold, C25/C26 cut 25 / >25, H25/H26 hike (2023-05+).",
        {"TLT": -1, "IEF": -1},
    ),
    Series(
        "cpi_mom",
        "kalshi",
        ("KXCPI",),
        "Month-over-month CPI, 'above X%' ladders per release.",
        {"TLT": -1, "IEF": -1, "GLD": 1},
    ),
    Series("cpi_yoy", "kalshi", ("KXCPIYOY",), "Year-over-year CPI, 'above X%' ladders per release.", {"TLT": -1}),
    Series("payrolls", "kalshi", ("KXPAYROLLS",), "Nonfarm payrolls change, 'above X' ladders.", {"QQQ": 1}),
    Series("unemployment", "kalshi", ("KXU3",), "U-3 unemployment rate, 'above X%' ladders.", {"QQQ": -1}),
    Series("gdp", "kalshi", ("KXGDP",), "Real GDP growth (annualised q/q), 'above X%' ladders.", {"QQQ": 1}),
    Series(
        "recession",
        "kalshi",
        ("KXRECSSNBER",),
        "Two consecutive negative GDP quarters in the named year or the one before (2022-07+).",
        {"QQQ": -1, "SPY": -1, "IEF": 1, "GLD": 1},
    ),
    Series(
        "shutdown",
        "kalshi",
        ("KXGOVSHUT", "KXGOVTSHUTDOWN"),
        "US federal government shut down on/by a date.",
        {"QQQ": -1},
    ),
    Series(
        "pm_recession",
        "polymarket",
        (
            "us-recession-in-2024-1",
            "us-recession-in-2025",
            "us-recession-by-end-of-2026",
            "us-recession-by-end-of-2027-20260807185409760",
        ),
        "Polymarket US recession by year-end (cross-check for Kalshi 'recession'; 2024-08+).",
        {"QQQ": -1, "IEF": 1},
    ),
    Series(
        "pm_shutdown",
        "polymarket",
        (
            "will-there-be-a-us-government-shutdown-by-october-2",
            "will-there-be-a-us-government-shutdown-by-november-19",
            "will-there-be-a-us-government-shutdown-by-jan-20",
            "will-there-be-a-us-government-shutdown-by-mar-9",
            "will-there-be-a-us-government-shutdown-by-mar-23",
            "us-government-shutdown-before-2025",
            "us-government-shutdown-in-2025",
            "us-government-shutdown-by-october-1",
            "will-there-be-another-us-government-shutdown-by-december-31",
            "will-there-be-another-us-government-shutdown-by-january-31",
            "another-us-government-shutdown-by-february-14",
            "government-shutdown-by-october-1-20260610162414910",
        ),
        "Polymarket US government shutdown by a date (2023-09+).",
        {"QQQ": -1},
    ),
)

BY_KEY = {s.key: s for s in SERIES}
