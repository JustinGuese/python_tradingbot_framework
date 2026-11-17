"""
Backfill SEC Form 13F structured data sets into inst13f_filings / inst13f_holdings.

Zip links are DISCOVERED from the SEC data-set page (names changed from
YYYYqN_form13f.zip to date ranges in 2024, and the newest file lives under a
different path). Resumable per file (progress file lists completed zip names),
rate-limited to 5 requests/second, generic User-Agent. Coverage starts 2013 Q2.
Writes to the configured POSTGRES_URI.

Usage:
    uv run python scripts/inst13f_backfill.py [--limit N]
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import urllib.request
import zipfile
from pathlib import Path

from tradingbot.utils.buyback_scan import DEFAULT_REQ_PER_SEC, SEC_USER_AGENT, TokenBucket
from tradingbot.utils.inst13f_ingest import ingest_quarter

PAGE_URL = "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"
PROGRESS = Path(os.environ.get("RESEARCH_CACHE", "/tmp")) / "inst13f_backfill_progress.json"
_LINK = re.compile(r'href="(/files/[^"]*?/form-13f-data-sets/([^"/]+_form13f\.zip))"')
_MONTHS = {
    m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)
}


def _sort_key(name: str) -> tuple[int, int, int]:
    """Chronological key from `2013q2_...` or `01mar2024-31may2024_...` (by range end)."""
    m = re.match(r"(\d{4})q(\d)_", name)
    if m:
        return int(m.group(1)), int(m.group(2)) * 3, 28
    m = re.match(r"\d{2}[a-z]{3}\d{4}-(\d{2})([a-z]{3})(\d{4})_", name)
    return (int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1))) if m else (0, 0, 0)


def discover_links(html: str) -> list[tuple[str, str]]:
    """Return [(zip name, absolute url)] sorted oldest to newest, de-duplicated."""
    found = {name: "https://www.sec.gov" + path for path, name in _LINK.findall(html)}
    return sorted(found.items(), key=lambda kv: _sort_key(kv[0]))


def _get(url: str, bucket: TokenBucket) -> bytes:
    bucket.acquire()
    req = urllib.request.Request(url, headers={"User-Agent": SEC_USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="max files to load this run")
    args = ap.parse_args()
    done = set(json.loads(PROGRESS.read_text())) if PROGRESS.exists() else set()
    bucket = TokenBucket(rate=DEFAULT_REQ_PER_SEC)
    links = discover_links(_get(PAGE_URL, bucket).decode("utf-8", errors="replace"))
    print(f"discovered {len(links)} zip files")
    loaded = 0
    for name, url in links:
        if name in done:
            continue
        if args.limit is not None and loaded >= args.limit:
            break
        zf = zipfile.ZipFile(io.BytesIO(_get(url, bucket)))
        read = lambda n: zf.read(n).decode("utf-8", errors="replace")  # noqa: E731, B023  # read() is only called within this iteration
        n_f, n_h = ingest_quarter(read("SUBMISSION.tsv"), read("COVERPAGE.tsv"), read("INFOTABLE.tsv"))
        print(f"{name}: {n_f} filings, {n_h} holdings")
        done.add(name)
        loaded += 1
        PROGRESS.write_text(json.dumps(sorted(done)))


if __name__ == "__main__":
    main()
