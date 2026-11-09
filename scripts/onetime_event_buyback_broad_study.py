"""
Buyback-authorization 8-K drift on S&P 400 / 600 (and S&P 100 re-run on the same window). 2026-11.

The S&P 100 study (event-buyback-2026-11.md) found one passing window (10 sessions, t 2.56) and the effect
sat in the smaller names. This is the out-of-sample test: S&P 400 and S&P 600 names the first study never saw.

PRE-REGISTERED (written before any result was read)
  * PRIMARY hypothesis, a single test: midsmall (S&P 400 + 600), hold 10 sessions, 10 bp per side.
    Verdict `edge` only if it clears the project bar (alpha vs QQQ t >= 2, both halves positive, beta < 0.5)
    AND the same-name random-date placebo p < 0.05.
  * SECONDARY grid (descriptive, not for promoting): index in {sp100, sp400, sp600, midsmall} x hold in {5, 10, 20}.
  * Window: filings since 2010-01-01. Event: 8-K Item 8.01/7.01 whose primary document matches the buyback regex
    of the S&P 100 study (same definition as the live scanner). 30-day per-company dedupe.
  * Costs: 10 bp per side primary (small caps), 2 / 25 bp shown. Entry at the first close strictly after acceptance.
  * Universe is the CURRENT S&P 400/600 list (survivorship-biased, as is any yfinance price history). The
    placebo keeps each event's name and randomises the date, so name-level survivorship and size premium
    are in the placebo too.

Reads the cache written by onetime_event_broad_fetch.py. Only filings whose document is in the cache were
checked; the run may be a random subset of all candidate 8-Ks (the fetch shuffles them), and the report says how much.

Usage: POSTGRES_URI=postgresql://x:x@localhost:5432/x PYTHONPATH=. python scripts/onetime_event_buyback_broad_study.py [--draws N]
"""

from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import scripts.event_book_lib as lib

OUT = Path("docs/backtests/event-buyback-broad-2026-11.md")
HOLDS = (5, 10, 20)
GROUPS = ("sp100", "sp400", "sp600", "midsmall")
PRIMARY = ("midsmall", 10)


def checked_fraction(cands: list[dict], k: int | None) -> tuple[list[dict], float]:
    """The first K candidates of the fetch's shuffled order that have been checked.

    The fetch shuffles the candidate list with Random(1), checks it in that order and checkpoints the matches every
    1,000 documents (the "buyback docs N/..." lines of its log). Pass that N as --k: the matches on disk cover at
    least the first N candidates of the shuffled order, and the study keeps exactly those. A prefix of a random
    shuffle is a random sample, and it keeps the 30-day dedupe close to exact. Without --k, all candidates are used.
    """
    import random

    order = list(cands)
    random.Random(1).shuffle(order)

    k = len(order) if k is None else min(k, len(order))
    return order[:k], k / max(len(order), 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--draws", type=int, default=3000)
    ap.add_argument(
        "--k", type=int, default=None, help="use the first K shuffled candidates (the fetch log's last checkpoint)"
    )
    args = ap.parse_args()
    a = args

    meta = pickle.loads((lib.BROAD_DIR / "candidates.pkl").read_bytes())
    uni: dict[str, str] = meta["universe"]
    done, frac = checked_fraction(meta["cands"], args.k)
    done_keys = {c["accession_number"] for c in done}
    matched = pickle.loads((lib.BROAD_DIR / "events_buyback_raw.pkl").read_bytes())
    matched = [m for m in matched if m["accession_number"] in done_keys]
    sp100_file = lib.BROAD_DIR / "events_buyback_sp100.pkl"
    sp100_complete = sp100_file.exists()
    if sp100_complete:  # every S&P 100 candidate was checked (onetime_event_sp100_fill.py), not just the sample
        matched = [m for m in matched if uni.get(m["symbol"]) != "sp100"] + pickle.loads(sp100_file.read_bytes())
    raw = pd.DataFrame(matched)
    raw["acceptance_dt"] = pd.to_datetime(raw["acceptance_dt"], utc=True)
    events = lib.dedupe(raw, 30)
    events["grp"] = events["symbol"].map(uni)
    print(
        f"candidates checked {len(done)}/{len(meta['cands'])} ({frac:.0%}); matched {len(raw)}; deduped {len(events)}"
    )
    print(events["grp"].value_counts().to_string())

    prices = lib.load_prices(sorted(uni))
    syms, idx, R, q = lib.build_matrix(prices)
    col = {s: i for i, s in enumerate(syms)}
    events = events[events["symbol"].isin(col)].reset_index(drop=True)
    events["entry"] = lib.entry_positions(idx, events["acceptance_dt"])
    events = events[events["entry"] < len(idx) - 2].reset_index(drop=True)
    events["sidx"] = events["symbol"].map(col)
    lo = int(events["entry"].min())
    print(f"events usable {len(events)}; book window {idx[lo].date()} -> {idx[-1].date()}")

    rows, results = [], {}
    for g in GROUPS:
        sub = events[events["grp"].isin(("sp400", "sp600") if g == "midsmall" else (g,))]
        for h in HOLDS:
            if len(sub) < 20:
                continue
            e = sub["entry"].to_numpy(dtype=int)
            s = sub["sidx"].to_numpy(dtype=int)
            res = {c: lib.evaluate_book(e, s, h, R, q, c, lo) for c in (0.0002, 0.001, 0.0025)}
            results[(g, h)] = res
            m = res[0.001]
            rows.append(
                f"| {g}{' (PRIMARY)' if (g, h) == PRIMARY else ''} | {h}d | {len(sub)} | {m['alpha']:+.1f}% | {m['t']:.2f} | "
                f"{m['beta']:.2f} | {m['a1']:+.1f}% ({m['t1']:.2f}) | {m['a2']:+.1f}% ({m['t2']:.2f}) | "
                f"{res[0.0002]['alpha']:+.1f}% ({res[0.0002]['t']:.2f}) | {res[0.0025]['alpha']:+.1f}% ({res[0.0025]['t']:.2f}) | "
                f"{'PASS' if lib.passes(m) else 'fail'} |"
            )
            print(rows[-1])

    # Placebo for the primary and for every secondary that passes the bar.
    pl_rows, pvals = [], {}
    todo = [k for k, r in results.items() if k in (PRIMARY, ("sp100", 10)) or lib.passes(r[0.001])]
    cols_by_group = {
        "midsmall": np.array([col[s] for s in uni if s in col and uni[s] in ("sp400", "sp600")], dtype=int),
        "sp400": np.array([col[s] for s in uni if s in col and uni[s] == "sp400"], dtype=int),
        "sp600": np.array([col[s] for s in uni if s in col and uni[s] == "sp600"], dtype=int),
        "sp100": np.array([col[s] for s in uni if s in col and uni[s] == "sp100"], dtype=int),
    }
    for g, h in todo:
        sub = events[events["grp"].isin(("sp400", "sp600") if g == "midsmall" else (g,))]
        e = sub["entry"].to_numpy(dtype=int)
        s = sub["sidx"].to_numpy(dtype=int)
        obs = results[(g, h)][0.001]["t"]
        p1, m1, q95 = lib.placebo(e, s, h, R, q, 0.001, lo, obs, a.draws, same_name=True)
        p3, m3, q953 = lib.placebo_same_dates(e, h, R, q, 0.001, lo, obs, cols_by_group[g], a.draws)
        p2, m2, q952 = lib.placebo(
            e, s, h, R, q, 0.001, lo, obs, a.draws, same_name=False, universe_cols=cols_by_group[g]
        )
        pvals[(g, h)] = p1
        pl_rows.append(
            f"| {g} {h}d | {obs:.2f} | {p1:.4f} | {m1:.2f} / {q95:.2f} | {p2:.4f} | {m2:.2f} / {q952:.2f} | {p3:.4f} | {m3:.2f} / {q953:.2f} |"
        )
        print(pl_rows[-1])

    car_rows = []
    for g in GROUPS:
        sub = events[events["grp"].isin(("sp400", "sp600") if g == "midsmall" else (g,))]
        for h in HOLDS:
            if len(sub) >= 20:
                car_rows.append(
                    lib.car_row(g, sub["entry"].to_numpy(dtype=int), sub["sidx"].to_numpy(dtype=int), h, R, q, lo)
                )
                print(car_rows[-1])

    prim = results.get(PRIMARY)
    if prim is None:
        verdict = "inconclusive (too few events)"
    else:
        m = prim[0.001]
        verdict = "edge" if lib.passes(m) and pvals[PRIMARY] < 0.05 else ("unproven" if m["t"] >= 1.5 else "no edge")

    n_by = events["grp"].value_counts().to_dict()
    text = [
        "# Buyback 8-K drift beyond the S&P 100 (generated)",
        "",
        f"**Primary verdict (midsmall, 10 sessions, 10 bp/side): `{verdict}`**. Generated by "
        "`scripts/onetime_event_buyback_broad_study.py`; the interpretation is added by hand below the tables.",
        "",
        "Pre-registered in the script docstring before any result was read: one primary test, a 12-cell descriptive grid, "
        "filings since 2010, same event definition as the S&P 100 study and the live scanner, 30-day dedupe.",
        "",
        f"- Candidate 8-Ks (Item 8.01/7.01) in the universe: {len(meta['cands'])}; documents checked: {len(done)} ({frac:.0%}, a random subset of S&P 400/600 filings)"
        + ("; every S&P 100 candidate was checked." if sp100_complete else "."),
        f"- Matched buyback filings: {len(raw)}; events after dedupe: {len(events)} ({', '.join(f'{k} {v}' for k, v in sorted(n_by.items()))}).",
        f"- Book window {idx[lo].date()} to {idx[-1].date()}. Alpha is vs QQQ; the headline columns are at 10 bp/side.",
        "- Universe = current constituents (survivorship-biased). Prices are yfinance adjusted closes.",
        "",
        "| Index | Hold | Events | Alpha/yr (10bp) | t | Beta | H1 alpha (t) | H2 alpha (t) | 2 bp alpha (t) | 25 bp alpha (t) | Bar |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
        *rows,
        "",
        f"## Placebo ({a.draws:,} random books per row, 10 bp/side, same event count)",
        "",
        "`same name`: each event keeps its symbol and gets a random date. `random name`: symbol also redrawn from the same index group. `same dates`: the real event dates with the names redrawn (isolates market timing: filings can cluster around market moves). The S&P 100 10-session row is a descriptive comparator, not part of the pre-registered primary.",
        "",
        "| Book | Observed t | p (same name) | same-name mean / 95th t | p (random name) | random-name mean / 95th t | p (same dates) | same-dates mean / 95th t |",
        "|---|---|---|---|---|---|---|---|",
        *pl_rows,
        "",
        "## Event level (one number per authorization; guards against a book carried by a few outliers)",
        "",
        "Cumulative log return over the hold minus QQQ's. The last column is the same statistic for random dates on the "
        "same names, which is what the mean should be compared with.",
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
