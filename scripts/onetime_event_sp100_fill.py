"""
Check the primary document of EVERY S&P 100 buyback candidate 8-K (not a sample), 2010 onward.

The broad fetch (onetime_event_broad_fetch.py) checks candidates in shuffled order and was stopped at a sample, which
leaves the S&P 100 group at a quarter of its filings. The S&P 100 is the group the original study (event-buyback-2026-11.md)
and the live BuybackDriftBot rest on, so it gets a complete pass here. Writes .cache/broad/events_buyback_sp100.pkl.

Usage: POSTGRES_URI=postgresql://x:x@localhost:5432/x PYTHONPATH=. python -u scripts/onetime_event_sp100_fill.py
"""

from __future__ import annotations

import pickle
import sys
from concurrent.futures import ThreadPoolExecutor

import scripts.onetime_event_broad_fetch as bf
import scripts.onetime_event_buyback_study as st


def main() -> int:
    meta = pickle.loads((bf.BROAD_DIR / "candidates.pkl").read_bytes())
    uni = meta["universe"]
    cands = [c for c in meta["cands"] if uni.get(c["symbol"]) == "sp100"]
    print(f"S&P 100 candidates: {len(cands)}", flush=True)

    def check(f: dict):
        try:
            t = bf.doc_text(f)
            return (dict(f) if t and st.matches_buyback_regex(t) else None), False
        except Exception:
            return None, True

    matched, bad = [], 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for i, (m, failed) in enumerate(pool.map(check, cands), 1):
            bad += failed
            if m is not None:
                matched.append(m)
            if i % 500 == 0:
                print(f"{i}/{len(cands)} matched {len(matched)} failures {bad}", flush=True)
    (bf.BROAD_DIR / "events_buyback_sp100.pkl").write_bytes(pickle.dumps(matched))
    print(f"done: {len(cands)} checked, {len(matched)} matched, {bad} failures", flush=True)
    return 0 if bad <= 0.02 * len(cands) else 1


if __name__ == "__main__":
    sys.exit(main())
