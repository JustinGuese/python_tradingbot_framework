"""
Print the current S&P 500 constituents from Wikipedia and diff them against
tradingbot.utils.universes.SP500.

Run by hand when refreshing the list — the bot never fetches constituents at
runtime, so an index change arrives as a reviewed diff, not as a silent
universe swap mid-backtest.

    POSTGRES_URI=stub:stub@localhost:5432/stub PYTHONPATH=. uv run python scripts/onetime_refresh_sp500.py
"""

import io
import os

os.environ.setdefault("POSTGRES_URI", "stub:stub@localhost:5432/stub")

import httpx
import pandas as pd

from tradingbot.utils.universes import SP500, SP500_EXCLUDED

URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def fetch() -> list[str]:
    html = httpx.get(URL, headers={"User-Agent": "tradingbot-universe-refresh"}, timeout=30).text
    for table in pd.read_html(io.StringIO(html)):
        if "Symbol" in table.columns and len(table) > 400:
            return sorted(str(s).strip() for s in table["Symbol"])
    raise RuntimeError("No constituents table found on the S&P 500 page")


def main() -> None:
    live = set(fetch())
    ours = set(SP500) | set(SP500_EXCLUDED)
    print(f"Wikipedia: {len(live)} symbols; ours: {len(SP500)} traded + {len(SP500_EXCLUDED)} excluded")
    print("Added to index (missing here):", sorted(live - ours) or "none")
    print("Removed from index (still here):", sorted(ours - live) or "none")
    print("\nCurrent list:\n" + ", ".join(f'"{s}"' for s in sorted(live)))


if __name__ == "__main__":
    main()
