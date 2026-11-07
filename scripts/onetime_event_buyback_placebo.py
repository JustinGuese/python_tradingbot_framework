"""
Fast vectorised placebo for the 10-day buyback variant (see onetime_event_buyback_study.py).

The study's own placebo rebuilds a full portfolio per draw (~20 s each). This keeps the same maths
(equal-weight book of names held `hold` sessions after entry, 2 bp/side, flat days = 0, t of the
daily alpha vs QQQ) on a precomputed return matrix, and first checks that it reproduces the
observed t before drawing. Draws pick random (date, symbol) pairs, same count as the real events.

Usage: POSTGRES_URI=postgresql://x:x@localhost:5432/x PYTHONPATH=. python scripts/onetime_event_buyback_placebo.py [--draws N] [--hold 10]
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

import scripts.onetime_event_buyback_study as st
from tradingbot.utils.universes import SP100


def load_inputs():
    symbols = list(SP100)
    t2c = st.load_sec_ticker_to_cik(use_cache=True)
    cand = []
    for s in symbols:
        cik = t2c.get(s)
        if cik:
            cand.extend(st.load_company_8k_filings(s, cik, start_date=st.DEFAULT_START_DATE, use_cache=True))
    matched = st.filter_filings_by_buyback_primary_doc(cand, use_cache=True)
    raw = pd.DataFrame(matched)
    raw["acceptance_dt"] = pd.to_datetime(raw["acceptance_dt"], utc=True)
    events = st.dedupe_events_by_symbol(raw, window_days=30)
    prices = st.load_price_history(symbols, start_date="2004-01-01", use_cache=True)
    return events, prices


def build_matrix(prices):
    syms = [s for s in prices if s != "QQQ" and prices[s] is not None and len(prices[s]) > 50]
    df = pd.DataFrame({s: prices[s].dropna()[~prices[s].dropna().index.duplicated(keep="last")] for s in syms}).sort_index()
    qqq = prices["QQQ"].dropna()
    qqq = qqq[~qqq.index.duplicated(keep="last")].sort_index()
    idx = df.index.union(qqq.index)
    df = df.reindex(idx)
    R = df.pct_change(fill_method=None).to_numpy(dtype=float)  # R[d, j] = return of sym j on day d
    q = qqq.reindex(idx).pct_change(fill_method=None).to_numpy(dtype=float)
    return syms, idx, R, q


def book_returns(entry_idx, sym_idx, hold, R, cost):
    """Equal-weight book: row d holds names with entry < d <= entry + hold (entry day = close of bar `entry`)."""
    n = R.shape[0]
    total = np.zeros(n)
    count = np.zeros(n)
    for k in range(1, hold + 1):
        d = entry_idx + k
        ok = d < n
        r = R[d[ok], sym_idx[ok]].copy()
        if k == 1:
            r = r - cost
        if k == hold:
            r = r - cost
        good = np.isfinite(r)
        np.add.at(total, d[ok][good], r[good])
        np.add.at(count, d[ok][good], 1.0)
    out = np.zeros(n)
    m = count > 0
    out[m] = total[m] / count[m]
    return out


def t_alpha(p, q):
    m = np.isfinite(q)
    p, q = p[m], q[m]
    beta = np.cov(p, q)[0, 1] / np.var(q, ddof=1)
    resid = p - beta * q
    return resid.mean() / resid.std(ddof=1) * np.sqrt(len(resid)), resid.mean() * 252, beta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--draws", type=int, default=5000)
    ap.add_argument("--hold", type=int, default=10)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    events, prices = load_inputs()
    syms, idx, R, q = build_matrix(prices)
    col = {s: i for i, s in enumerate(syms)}
    ev = events[events["symbol"].isin(col)]
    # entry bar = first daily close strictly after acceptance time (UTC close approximated by date + 21:00 UTC)
    close_utc = (idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")) + pd.Timedelta(hours=21)
    ent = np.searchsorted(close_utc.values, ev["acceptance_dt"].dt.tz_convert("UTC").dt.tz_localize(None).values, side="right")
    keep = ent < len(idx)
    entry = ent[keep].astype(int)
    sidx = np.array([col[s] for s in ev["symbol"].to_numpy()[keep]], dtype=int)

    cost = 0.0002
    p = book_returns(entry, sidx, a.hold, R, cost)
    t_obs, alpha_obs, beta_obs = t_alpha(p[1:], q[1:])
    print(f"events used {len(entry)}; observed (vectorised) t {t_obs:.2f}, alpha {alpha_obs * 100:+.2f}%/yr, beta {beta_obs:.2f}")
    print("(study reported t 2.56 / +10.00% for hold 10d, all events; these should be close)")

    rng = np.random.default_rng(a.seed)
    first_valid = int(np.nanargmax(np.isfinite(R).sum(axis=1) > 50))
    exceed = 0
    ts = []
    for _ in range(a.draws):
        e = rng.integers(first_valid, len(idx) - a.hold - 1, size=len(entry))
        s = rng.integers(0, len(syms), size=len(entry))
        pr = book_returns(e, s, a.hold, R, cost)
        t, _, _ = t_alpha(pr[1:], q[1:])
        ts.append(t)
        exceed += t >= t_obs
    ts = np.array(ts)
    p_ref = float((ts >= 2.56).mean())
    print(f"placebo draws {a.draws}: mean t {ts.mean():.2f}, sd {ts.std():.2f}, 95th pct {np.percentile(ts, 95):.2f}, p(t>=obs) = {exceed / a.draws:.4f}, p(t>=2.56, the study's value) = {p_ref:.4f}")


if __name__ == "__main__":
    sys.exit(main())
