"""
Scrape the S&P 500 historical changes table from Wikipedia into
data/sp500_changes.csv.

Run by hand when updating index history:

    PYTHONPATH=. uv run python scripts/onetime_sp500_membership.py
"""

import io
from pathlib import Path

import httpx
import pandas as pd

URLS = [
    "https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500",
    "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
]

OUTPUT_PATH = Path(__file__).resolve().parents[1] / "data" / "sp500_changes.csv"


def clean_ticker(val: object) -> str:
    if pd.isna(val):
        return ""
    s = str(val).replace("|", "").strip()
    return "" if s.lower() == "nan" else s


def fetch_changes() -> pd.DataFrame:
    headers = {"User-Agent": "tradingbot-sp500-membership"}
    for url in URLS:
        try:
            resp = httpx.get(url, headers=headers, timeout=30)
            if resp.status_code != 200:
                continue
            tables = pd.read_html(io.StringIO(resp.text))
            for table in tables:
                # Find the changes table which has Date / Effective Date, Added, Removed
                col_strs = [
                    " ".join(str(sub) for sub in c).lower() if isinstance(c, tuple) else str(c).lower()
                    for c in table.columns
                ]
                has_date = any("date" in c for c in col_strs)
                has_added = any("added" in c for c in col_strs)
                has_removed = any("removed" in c for c in col_strs)
                if has_date and (has_added or has_removed):
                    # Identify specific columns
                    date_col = None
                    added_col = None
                    removed_col = None
                    for orig_col, c_str in zip(table.columns, col_strs, strict=False):
                        if "date" in c_str and date_col is None:
                            date_col = orig_col
                        elif "added" in c_str and ("ticker" in c_str or "symbol" in c_str) and added_col is None:
                            added_col = orig_col
                        elif "removed" in c_str and ("ticker" in c_str or "symbol" in c_str) and removed_col is None:
                            removed_col = orig_col

                    if date_col is not None and (added_col is not None or removed_col is not None):
                        dates = pd.to_datetime(table[date_col], format="mixed", errors="coerce").dt.strftime("%Y-%m-%d")
                        added = (
                            table[added_col].apply(clean_ticker)
                            if added_col is not None
                            else pd.Series([""] * len(table))
                        )
                        removed = (
                            table[removed_col].apply(clean_ticker)
                            if removed_col is not None
                            else pd.Series([""] * len(table))
                        )

                        df = pd.DataFrame({"date": dates, "added": added, "removed": removed})
                        df = df.dropna(subset=["date"])
                        df = df[df["date"] != ""]
                        df = df[~((df["added"] == "") & (df["removed"] == ""))]
                        if len(df) > 50:
                            # Sort reverse chronologically (latest first)
                            df = df.sort_values("date", ascending=False, kind="mergesort").reset_index(drop=True)
                            return df
        except Exception as e:
            print(f"Error fetching from {url}: {e}")

    raise RuntimeError("Could not find S&P 500 changes table on Wikipedia")


def main() -> None:
    print("Fetching S&P 500 changes table...")
    df = fetch_changes()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_PATH, index=False)
    print(f"Wrote {len(df)} rows to {OUTPUT_PATH}")
    print(f"Date range: {df['date'].min()} to {df['date'].max()}")
    print("\nRecent changes:")
    print(df.head(5).to_string(index=False))


if __name__ == "__main__":
    main()
