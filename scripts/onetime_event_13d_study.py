"""
Initial Schedule 13D filings against S&P 100/400/600 names: is there post-filing drift? (2026-11)

Activist and large-holder 13Ds are a documented source of abnormal returns. Most of the move happens at the
filing, which a daily-close strategy partly misses (entry is the first close strictly after the acceptance
time), so this tests what is left to buy afterwards.

PRE-REGISTERED (written before any result was read)
  * Event: initial SC 13D / SCHEDULE 13D (amendments dropped) with the subject in the S&P 100/400/600 list,
    filed since 2010-01-01, 30-day per-company dedupe.
  * "Fund-like" filer: a reporting-person name on the cover matches FUND_RE below. Fixed before looking.
  * Grid: filer in {all, fund-like} x hold in {10, 20, 60} sessions = 6 cells, 10 bp per side
    (2 and 25 bp also shown).
  * PRIMARY, a single test: fund-like, 20 sessions. `edge` only if alpha vs QQQ t >= 2, positive in both
    halves, beta < 0.5, AND the same-name random-date placebo p < 0.05. Fewer than 40 usable events in the
    primary cell is reported as inconclusive, not as a result.
  * Universe is current constituents (survivorship-biased); the placebo keeps each event's name.

Reads the cache written by onetime_event_broad_fetch.py.

Usage: POSTGRES_URI=postgresql://x:x@localhost:5432/x PYTHONPATH=. python scripts/onetime_event_13d_study.py [--draws N]
"""

from __future__ import annotations

import argparse
import pickle
import re
import sys
from pathlib import Path

import pandas as pd

import scripts.event_book_lib as lib

OUT = Path("docs/backtests/event-13d-2026-11.md")
HOLDS = (10, 20, 60)
PRIMARY = ("fund-like", 20)
FUND_RE = re.compile(
    r"\b(capital|partners|management|fund|funds|advisors|advisers|investment|investments|investors|asset|"
    r"l\.?p\.?|master|offshore|value|opportunit\w*|activist|trian|icahn|elliott|starboard|jana|pershing)\b",
    re.I,
)
NAME_RE = re.compile(
    r"NAMES?\s+OF\s+REPORTING\s+PERSONS?\W{0,10}(.{3,300}?)(?:I\.?R\.?S\.?\s|CHECK\s+THE\s+APPROPRIATE|\b2\s*\.?\s+CHECK)",
    re.I | re.S,
)


def reporting_names(text: str) -> str | None:
    m = NAME_RE.search(text or "")
    return m.group(1).strip() if m else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--draws", type=int, default=3000)
    a = ap.parse_args()

    meta = pickle.loads((lib.BROAD_DIR / "candidates.pkl").read_bytes())
    uni: dict[str, str] = meta["universe"]
    docs = pickle.loads((lib.BROAD_DIR / "events_13d_raw.pkl").read_bytes())
    raw = pd.DataFrame(docs)
    raw["acceptance_dt"] = pd.to_datetime(raw["acceptance_dt"], utc=True)
    raw["names"] = raw["text"].map(reporting_names)
    # The fetch kept only the first 6,000 characters of each document, but the post-2024 SCHEDULE 13D format puts the
    # cover page ~7,000+ characters in. Re-read those filings in full from the on-disk cache (no network) so their
    # filers are classified; found 2026-11-09 while smoke-testing the live scanner.
    import scripts.onetime_event_broad_fetch as bf

    for i in raw.index[raw["names"].isna()]:
        try:
            full = bf.doc_text(raw.loc[i].to_dict())
        except Exception:
            continue
        raw.at[i, "names"] = reporting_names(full)
    n_named = int(raw["names"].notna().sum())
    raw["fund"] = raw["names"].map(lambda s: isinstance(s, str) and bool(FUND_RE.search(s)))
    events = lib.dedupe(raw, 30)
    print(
        f"initial 13Ds {len(raw)}; with a parsed filer name {n_named}; after dedupe {len(events)}; fund-like {int(events['fund'].sum())}"
    )

    prices = lib.load_prices(sorted(uni))
    syms, idx, R, q = lib.build_matrix(prices)
    col = {s: i for i, s in enumerate(syms)}
    events = events[events["symbol"].isin(col)].reset_index(drop=True)
    events["entry"] = lib.entry_positions(idx, events["acceptance_dt"])
    events = events[events["entry"] < len(idx) - 2].reset_index(drop=True)
    events["sidx"] = events["symbol"].map(col)
    lo = int(events["entry"].min())

    cells = {"all": events, "fund-like": events[events["fund"]]}
    rows, results = [], {}
    for name, sub in cells.items():
        for h in HOLDS:
            if len(sub) < 20:
                continue
            e = sub["entry"].to_numpy(dtype=int)
            s = sub["sidx"].to_numpy(dtype=int)
            res = {c: lib.evaluate_book(e, s, h, R, q, c, lo) for c in (0.0002, 0.001, 0.0025)}
            results[(name, h)] = res
            m = res[0.001]
            rows.append(
                f"| {name}{' (PRIMARY)' if (name, h) == PRIMARY else ''} | {h}d | {len(sub)} | {m['alpha']:+.1f}% | {m['t']:.2f} | "
                f"{m['beta']:.2f} | {m['a1']:+.1f}% ({m['t1']:.2f}) | {m['a2']:+.1f}% ({m['t2']:.2f}) | "
                f"{res[0.0002]['alpha']:+.1f}% ({res[0.0002]['t']:.2f}) | {res[0.0025]['alpha']:+.1f}% ({res[0.0025]['t']:.2f}) | "
                f"{'PASS' if lib.passes(m) else 'fail'} |"
            )
            print(rows[-1])

    pl_rows, pvals = [], {}
    # Placebo on every cell (descriptive; the verdict still rests on the single pre-registered primary).
    for key in results:
        name, h = key
        sub = cells[name]
        e = sub["entry"].to_numpy(dtype=int)
        s = sub["sidx"].to_numpy(dtype=int)
        obs = results[key][0.001]["t"]
        p1, m1, q95 = lib.placebo(e, s, h, R, q, 0.001, lo, obs, a.draws, same_name=True)
        pvals[key] = p1
        pl_rows.append(f"| {name} {h}d | {obs:.2f} | {p1:.4f} | {m1:.2f} / {q95:.2f} |")
        print(pl_rows[-1])

    car_rows = [
        lib.car_row(name, sub["entry"].to_numpy(dtype=int), sub["sidx"].to_numpy(dtype=int), h, R, q, lo)
        for name, sub in cells.items()
        for h in HOLDS
        if len(sub) >= 20
    ]
    for r in car_rows:
        print(r)

    n_primary = len(cells[PRIMARY[0]])
    if PRIMARY not in results or n_primary < 40:
        verdict = f"inconclusive ({n_primary} usable fund-like events)"
    else:
        m = results[PRIMARY][0.001]
        verdict = "edge" if lib.passes(m) and pvals[PRIMARY] < 0.05 else ("unproven" if m["t"] >= 1.5 else "no edge")

    text = [
        "# Schedule 13D filings: post-filing drift on S&P 100/400/600 names (generated)",
        "",
        f"**Primary verdict (fund-like filer, 20 sessions, 10 bp/side): `{verdict}`**. Generated by "
        "`scripts/onetime_event_13d_study.py`; interpretation is added by hand below the tables.",
        "",
        "Pre-registered in the script docstring: one primary test, a 6-cell grid, filings since 2010, 30-day dedupe, "
        "entry at the first close strictly after the acceptance time.",
        "",
        f"- Initial 13D filings found: {len(raw)}; filer name parsed from the cover for {n_named}; events after dedupe: {len(events)}; "
        f"fund-like filers: {int(events['fund'].sum())}.",
        f"- Book window {idx[lo].date()} to {idx[-1].date()}. Alpha is vs QQQ; headline columns at 10 bp/side.",
        "- The filer classifier is a keyword list on the cover-page name, so it is crude: it will call some passive holders "
        "'fund-like' and miss activists with plain names.",
        "",
        "| Filer | Hold | Events | Alpha/yr (10bp) | t | Beta | H1 alpha (t) | H2 alpha (t) | 2 bp alpha (t) | 25 bp alpha (t) | Bar |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
        *rows,
        "",
        "## Placebo (random dates, same names, same count)",
        "",
        "| Book | Observed t | p (same name) | mean / 95th t |",
        "|---|---|---|---|",
        *pl_rows,
        "",
        "## Event level (one number per filing; the book's t can be carried by a few outliers)",
        "",
        "Cumulative log return over the hold minus QQQ's, per event. The last column is the same statistic for random dates "
        "on the same names, which is what the mean should be compared with.",
        "",
        lib.CAR_HEADER,
        *car_rows,
        "",
    ]
    OUT.write_text("\n".join(text), encoding="utf-8")
    print("\n".join(text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
