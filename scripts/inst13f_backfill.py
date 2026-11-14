"""
Backfill SEC Form 13F structured data sets into inst13f_filings / inst13f_holdings.

Resumable per quarter (progress file lists completed quarters), rate-limited to
5 requests/second, generic User-Agent. Writes to the configured POSTGRES_URI.

Usage:
    uv run python scripts/inst13f_backfill.py --start 2013q2 --end 2026q3
"""

from __future__ import annotations

import argparse
import io
import json
import os
import urllib.request
import zipfile
from pathlib import Path

from tradingbot.utils.buyback_scan import DEFAULT_REQ_PER_SEC, SEC_USER_AGENT, TokenBucket
from tradingbot.utils.inst13f_ingest import ingest_quarter

BASE_URL = "https://www.sec.gov/files/structureddata/data/form-13f-data-sets/{q}_form13f.zip"
PROGRESS = Path(os.environ.get("RESEARCH_CACHE", "/tmp")) / "inst13f_backfill_progress.json"


def quarters(start: str, end: str) -> list[str]:
    y, q = int(start[:4]), int(start[-1])
    ey, eq = int(end[:4]), int(end[-1])
    out = []
    while (y, q) <= (ey, eq):
        out.append(f"{y}q{q}")
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return out


def fetch_zip(url: str, bucket: TokenBucket) -> zipfile.ZipFile:
    bucket.acquire()
    req = urllib.request.Request(url, headers={"User-Agent": SEC_USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return zipfile.ZipFile(io.BytesIO(resp.read()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2013q2")
    ap.add_argument("--end", default="2026q3")
    args = ap.parse_args()
    done = set(json.loads(PROGRESS.read_text())) if PROGRESS.exists() else set()
    bucket = TokenBucket(rate=DEFAULT_REQ_PER_SEC)
    for q in quarters(args.start, args.end):
        if q in done:
            continue
        zf = fetch_zip(BASE_URL.format(q=q), bucket)
        read = lambda name: zf.read(name).decode("utf-8", errors="replace")  # noqa: E731
        n_f, n_h = ingest_quarter(read("SUBMISSION.tsv"), read("COVERPAGE.tsv"), read("INFOTABLE.tsv"))
        print(f"{q}: {n_f} filings, {n_h} holdings")
        done.add(q)
        PROGRESS.write_text(json.dumps(sorted(done)))


if __name__ == "__main__":
    main()
