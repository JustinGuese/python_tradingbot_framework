"""
Shared EDGAR fetch for the broad-universe buyback study and the 13D activist study (2026-11).

One pass per company over its submissions JSON picks out two kinds of filings from START_DATE:
  * 8-Ks with Item 8.01 / 7.01 (buyback candidates; the primary document is regex-checked),
  * initial Schedule 13D filings made against the company (SC 13D, SCHEDULE 13D; amendments dropped).

Universe: S&P 100 + S&P 400 + S&P 600 *current* constituents (Wikipedia). That is survivorship-biased
by construction (delisted names are missing, as they are from the yfinance prices); the downstream
studies lean on a same-universe placebo to separate that from the event effect.

Everything is cached on disk (.cache/edgar and .cache/broad), so the script is resumable.

Usage: POSTGRES_URI=postgresql://x:x@localhost:5432/x PYTHONPATH=. python -u scripts/onetime_event_broad_fetch.py
"""

from __future__ import annotations

import io
import json
import pickle
import random
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

import scripts.onetime_event_buyback_study as st
from tradingbot.utils.universes import SP100

START_DATE = "2010-01-01"
FETCH_RATE_PER_SEC = 7.0  # SEC's cap is 10 req/s; stay well under it
st.RATE_LIMITER = st.RateLimiter(FETCH_RATE_PER_SEC)
BROAD_DIR = Path(".cache/broad")
WIKI = {
    "sp400": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
    "sp600": "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
}
FORMS_13D = {"SC 13D", "SCHEDULE 13D"}


def load_universe() -> dict[str, str]:
    """symbol -> index tag ('sp100' | 'sp400' | 'sp600'); an S&P 100 name keeps the sp100 tag."""
    f = BROAD_DIR / "universe.pkl"
    if f.exists():
        return pickle.loads(f.read_bytes())
    uni: dict[str, str] = {}
    for tag, url in WIKI.items():
        r = requests.get(url, headers={"User-Agent": st.SEC_USER_AGENT}, timeout=30)
        r.raise_for_status()
        df = pd.read_html(io.StringIO(r.text))[0]
        for sym in df["Symbol"].astype(str):
            uni.setdefault(sym.strip().replace(".", "-"), tag)
    for sym in SP100:
        uni[sym] = "sp100"
    BROAD_DIR.mkdir(parents=True, exist_ok=True)
    f.write_bytes(pickle.dumps(uni))
    return uni


def parse_submissions(symbol: str, cik: int) -> tuple[list[dict], list[dict]]:
    """Return (8-K buyback candidates, initial 13D filings) for one company since START_DATE."""
    cik_str = str(cik).zfill(10)
    cands: list[dict] = []
    d13: list[dict] = []

    def group(g: dict) -> None:
        forms = g.get("form", [])
        for i, form in enumerate(forms):
            form = str(form).upper()
            fdate = str(g["filingDate"][i])
            if fdate < START_DATE:
                continue
            acc = str(g["accessionNumber"][i])
            pdoc = str(g["primaryDocument"][i]) if i < len(g.get("primaryDocument", [])) else ""
            acc_dt = str(g["acceptanceDateTime"][i]) if i < len(g.get("acceptanceDateTime", [])) else None
            row = {
                "symbol": symbol,
                "cik": cik,
                "filing_date": fdate,
                "acceptance_dt": st.parse_acceptance_datetime(acc_dt, fdate),
                "accession_number": acc,
                "primary_document": pdoc,
            }
            if form == "8-K":
                items = g["items"][i] if i < len(g.get("items", [])) else None
                if st.has_item_801_or_701(items):
                    cands.append({**row, "items": items})
            elif form in FORMS_13D:
                d13.append({**row, "form": form})

    raw = st.fetch_edgar_bytes(
        f"https://data.sec.gov/submissions/CIK{cik_str}.json",
        cache_file=st.SUBMISSIONS_CACHE_DIR / f"CIK{cik_str}.json",
    )
    data = json.loads(raw.decode("utf-8"))
    group(data.get("filings", {}).get("recent", {}))
    for of in data.get("filings", {}).get("files", []):
        if of.get("filingTo", "9999") < START_DATE or not of.get("name"):
            continue
        try:
            oraw = st.fetch_edgar_bytes(
                f"https://data.sec.gov/submissions/{of['name']}",
                cache_file=st.SUBMISSIONS_CACHE_DIR / of["name"],
            )
            group(json.loads(oraw.decode("utf-8")))
        except Exception as exc:
            warnings.warn(f"{symbol}: older submissions file {of['name']} failed: {exc}", stacklevel=2)
    return cands, d13


def doc_text(f: dict) -> str | None:
    acc_clean = f["accession_number"].replace("-", "")
    if not f["primary_document"]:
        return None
    url = f"https://www.sec.gov/Archives/edgar/data/{f['cik']}/{acc_clean}/{f['primary_document']}"
    raw = st.fetch_edgar_bytes(url, cache_file=st.DOCS_CACHE_DIR / f"{f['cik']}_{acc_clean}_{f['primary_document']}")
    return st.clean_html(raw.decode("utf-8", errors="replace"))


def main() -> int:
    uni = load_universe()
    print(
        f"universe {len(uni)}: "
        + ", ".join(f"{t}={sum(v == t for v in uni.values())}" for t in ("sp100", "sp400", "sp600")),
        flush=True,
    )
    t2c = st.load_sec_ticker_to_cik(use_cache=True)
    missing = [s for s in uni if s not in t2c]
    print(f"{len(missing)} symbols without a CIK (skipped): {missing[:15]}", flush=True)
    syms = [s for s in uni if s in t2c]

    cands: list[dict] = []
    d13: list[dict] = []
    fails = 0
    done = 0

    def one(s: str):
        try:
            return parse_submissions(s, t2c[s]), None
        except Exception as exc:
            return None, exc

    with ThreadPoolExecutor(max_workers=8) as pool:
        for res, exc in pool.map(one, syms):
            done += 1
            if exc is not None:
                fails += 1
                continue
            cands.extend(res[0])
            d13.extend(res[1])
            if done % 100 == 0:
                print(
                    f"submissions {done}/{len(syms)}  8-K candidates {len(cands)}  13D initials {len(d13)}  fails {fails}",
                    flush=True,
                )
    print(
        f"submissions done: {len(cands)} 8-K candidates, {len(d13)} initial 13Ds, {fails} failed companies", flush=True
    )
    if fails > 0.1 * len(syms):
        print("too many failed companies, aborting", flush=True)
        return 1
    (BROAD_DIR / "candidates.pkl").write_bytes(pickle.dumps({"cands": cands, "d13": d13, "universe": uni}))

    # 13D docs first (few, and they carry the filer name), then buyback candidate docs.
    def fetch13(f: dict):
        try:
            t = doc_text(f)
            return {**f, "text": (t or "")[:6000]}, False
        except Exception:
            return None, True

    with ThreadPoolExecutor(max_workers=8) as pool:
        out = list(pool.map(fetch13, d13))
    d13_docs = [o for o, bad in out if o is not None]
    print(f"13D docs fetched {len(d13_docs)}/{len(d13)}", flush=True)
    (BROAD_DIR / "events_13d_raw.pkl").write_bytes(pickle.dumps(d13_docs))

    random.Random(1).shuffle(cands)  # so a partial run is not biased to one end of the alphabet
    matched: list[dict] = []
    bad = 0
    n = 0

    def checkbb(f: dict):
        try:
            t = doc_text(f)
            return (dict(f) if t and st.matches_buyback_regex(t) else None), False
        except Exception:
            return None, True

    with ThreadPoolExecutor(max_workers=8) as pool:
        for m, failed in pool.map(checkbb, cands):
            n += 1
            bad += failed
            if m is not None:
                matched.append(m)
            if n % 1000 == 0:
                print(f"buyback docs {n}/{len(cands)}  matched {len(matched)}  fetch failures {bad}", flush=True)
                (BROAD_DIR / "events_buyback_raw.pkl").write_bytes(pickle.dumps(matched))
    print(f"buyback docs done: {n} checked, {len(matched)} matched, {bad} fetch failures", flush=True)
    (BROAD_DIR / "events_buyback_raw.pkl").write_bytes(pickle.dumps(matched))
    if bad > 0.1 * max(n, 1):
        print("WARNING: >10% doc fetch failures; rerun to retry (cache makes it resumable)", flush=True)
        return 1
    print("EXIT-OK", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
