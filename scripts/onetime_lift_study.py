"""
Which of the new bots can legitimately be lifted over t > 1, and by what route? (2026-11-09)

Two levers, fixed before any result was read, nothing fitted, no parameter touched:

  1. CASH YIELD. Paper bots hold 0%-yield USD, but the live copier parks idle weight in SHV (CLAUDE.md), so a bot's measured
     alpha understates the copied book by roughly rf x its cash share. The 13D and buyback bots are mostly cash. Cash earns the
     13-week T-bill yield (^IRX / 252 per day).
  2. BLENDING. An equal-weight (1/4 each, no fitting) blend of the four bots built this month. Averaging helps t only to the
     extent the alpha streams are independent; the correlation table says how much that is.

Streams (each reproduces the bot's rule; the gate books use the validated simulator of onetime_gate_other_markets.py, the event
books use the bots' real sizing: equal weight capped at 0.5 per name, remainder in cash):
  us_sector_gate   QuantsmdSmaDailyGatedBot rule (nine SPDR sectors, SPY gate)
  country_gate     CountryTrendGateBot (20 country ETFs, own-index gate)
  global_sector_gate  the global-sector gate book (NOT a built bot; t < 1 in its own study)
  buyback_sp100    BuybackDriftBot (S&P 100, 10 sessions)
  activist_13d     Activist13DBot (fund-like 13D, 20 sessions)
Event books are 2010-02 onward (the EDGAR window), so the comparison window is 2010-02 to now for every stream.
Costs: 5 bp per unit of turnover for the gate books, 10 bp per side for the event books.

Usage: POSTGRES_URI=postgresql://x:x@localhost:5432/x PYTHONPATH=. python scripts/onetime_lift_study.py
"""

from __future__ import annotations

import contextlib
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

import scripts.event_book_lib as lib
import scripts.onetime_event_13d_study as s13
import scripts.onetime_gate_other_markets as gate
from tradingbot.utils.alpha_report import alpha_stats

OUT = Path("docs/backtests/lift-over-t1-2026-11.md")
CAP = 0.5
EVENT_COST = 0.001
START = "2010-02-01"


def tbill_daily(index: pd.DatetimeIndex) -> pd.Series:
    irx = yf.download("^IRX", start="1996-01-01", auto_adjust=False, progress=False)["Close"].squeeze()
    irx.index = pd.DatetimeIndex(irx.index).tz_localize(None).normalize()
    return (irx / 100.0 / 252.0).reindex(index.union(irx.index)).ffill().reindex(index).fillna(0.0)


def gate_stream(universe: str, gate_kind: str, px: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """(net return, cash share) of a gate book, from the validated simulator."""
    closes = px[[m for m in gate.UNIVERSES[universe] if m in px.columns and px[m].notna().sum() > 400]].dropna(
        how="all"
    )
    g = gate.gate_series(gate.ew_index(closes)) if gate_kind == "own" else gate.gate_series(px["SPY"].dropna())
    w = gate.book_weights(closes, g)
    ret = gate.simulate(closes, w)
    invested = w.shift(1).fillna(0.0).sum(axis=1)
    return ret, (1.0 - invested).clip(lower=0.0)


def event_stream(entry, sidx, hold, R, idx) -> tuple[pd.Series, pd.Series]:
    """Bot-faithful event book: equal weight min(1/n, CAP) over the n active names, rest in cash."""
    n = R.shape[0]
    total = np.zeros(n)
    count = np.zeros(n)
    for k in range(1, hold + 1):
        d = entry + k
        ok = d < n
        r = R[d[ok], sidx[ok]].copy()
        if k == 1:
            r = r - EVENT_COST
        if k == hold:
            r = r - EVENT_COST
        good = np.isfinite(r)
        np.add.at(total, d[ok][good], r[good])
        np.add.at(count, d[ok][good], 1.0)
    invested = np.minimum(1.0, CAP * count)
    ret = np.divide(total, count, out=np.zeros(n), where=count > 0) * invested
    return pd.Series(ret, index=idx), pd.Series(1.0 - invested, index=idx)


def load_events(uni: dict[str, str]):
    bb = pd.DataFrame(pickle.loads((lib.BROAD_DIR / "events_buyback_sp100.pkl").read_bytes()))
    bb["acceptance_dt"] = pd.to_datetime(bb["acceptance_dt"], utc=True)
    bb = lib.dedupe(bb, 30)
    raw = pd.DataFrame(pickle.loads((lib.BROAD_DIR / "events_13d_raw.pkl").read_bytes()))
    raw["acceptance_dt"] = pd.to_datetime(raw["acceptance_dt"], utc=True)
    raw["names"] = raw["text"].map(s13.reporting_names)
    import scripts.onetime_event_broad_fetch as bf

    for i in raw.index[raw["names"].isna()]:  # full cached text: the post-2024 cover is ~7,000 chars in
        with contextlib.suppress(Exception):
            raw.at[i, "names"] = s13.reporting_names(bf.doc_text(raw.loc[i].to_dict()))
    raw["fund"] = raw["names"].map(lambda s: isinstance(s, str) and bool(s13.FUND_RE.search(s)))
    d13 = lib.dedupe(raw, 30)
    return bb, d13[d13["fund"]].reset_index(drop=True)


def stats(r: pd.Series, q: pd.Series) -> dict:
    a = alpha_stats(r, q)
    if a is None:
        return {}
    mid = r.index[len(r) // 2]
    h1, h2 = alpha_stats(r[r.index < mid], q), alpha_stats(r[r.index >= mid], q)
    return {
        "alpha": a.alpha * 100, "t": a.t, "beta": a.beta, "corr": a.corr,
        "t1": h1.t if h1 else float("nan"), "t2": h2.t if h2 else float("nan"),
        "a1": h1.alpha * 100 if h1 else float("nan"), "a2": h2.alpha * 100 if h2 else float("nan"),
    }  # fmt: skip


def row(name: str, r: pd.Series, q: pd.Series, cash: pd.Series | None = None) -> str:
    s = stats(r, q)
    cs = f"{cash.reindex(r.index).mean() * 100:.0f}%" if cash is not None else "n/a"
    return (
        f"| {name} | {cs} | {s['alpha']:+.1f}% | {s['t']:.2f} | {s['beta']:.2f} | {s['corr']:.2f} | "
        f"{s['a1']:+.1f}% ({s['t1']:.2f}) | {s['a2']:+.1f}% ({s['t2']:.2f}) |"
    )


def main() -> int:
    meta = pickle.loads((lib.BROAD_DIR / "candidates.pkl").read_bytes())
    uni = meta["universe"]
    prices = lib.load_prices(sorted(uni))
    syms, idx, R, q = lib.build_matrix(prices)
    col = {s: i for i, s in enumerate(syms)}
    qqq = pd.Series(q, index=idx)
    rf = tbill_daily(idx)

    px = gate.load_prices()
    streams: dict[str, tuple[pd.Series, pd.Series]] = {
        "us_sector_gate": gate_stream("us_sectors", "spy", px),
        "country_gate": gate_stream("countries", "own", px),
        "global_sector_gate": gate_stream("global_sectors", "own", px),
    }
    bb, d13 = load_events(uni)
    for name, ev, hold in (("buyback_sp100", bb, 10), ("activist_13d", d13, 20)):
        ev = ev[ev["symbol"].isin(col)].copy()
        ev["entry"] = lib.entry_positions(idx, ev["acceptance_dt"])
        ev = ev[ev["entry"] < len(idx) - 2]
        streams[name] = event_stream(
            ev["entry"].to_numpy(dtype=int), ev["symbol"].map(col).to_numpy(dtype=int), hold, R, idx
        )

    window = slice(pd.Timestamp(START), None)
    qw = qqq.loc[window].dropna()

    def clip(s: pd.Series) -> pd.Series:
        s = s.copy()
        s.index = pd.DatetimeIndex(s.index).tz_localize(None).normalize() if s.index.tz is None else s.index
        return s.reindex(qw.index).fillna(0.0)

    base = {k: clip(v[0]) for k, v in streams.items()}
    cash = {k: clip(v[1]) for k, v in streams.items()}
    rfw = rf.reindex(qw.index).fillna(0.0)
    lifted = {k: base[k] + cash[k] * rfw for k in base}

    hdr = "| Stream | Avg cash | Alpha/yr | t | Beta | Corr | H1 alpha (t) | H2 alpha (t) |\n|---|---|---|---|---|---|---|---|"
    t0 = [row(k, base[k], qw, cash[k]) for k in base]
    t1 = [row(k, lifted[k], qw, cash[k]) for k in base]

    built = ["us_sector_gate", "country_gate", "buyback_sp100", "activist_13d"]
    blend0 = sum(base[k] for k in built) / len(built)
    blend1 = sum(lifted[k] for k in built) / len(built)
    corr = pd.DataFrame({k: base[k] for k in base}).corr().round(2)
    resid = pd.DataFrame(
        {k: base[k] - (np.cov(base[k], qw)[0, 1] / np.var(qw, ddof=1)) * qw for k in base}
    ).corr().round(2)  # fmt: skip

    def md(df: pd.DataFrame) -> str:
        return (
            "| | "
            + " | ".join(df.columns)
            + " |\n|---|"
            + "---|" * len(df.columns)
            + "\n"
            + "\n".join(f"| {i} | " + " | ".join(f"{v:.2f}" for v in df.loc[i]) + " |" for i in df.index)
        )

    text = [
        "# Which of the new bots can be lifted over t > 1? (generated)",
        "",
        "Generated by `scripts/onetime_lift_study.py`; the interpretation is added by hand below. Levers fixed in the script docstring "
        "before any result was read: cash yield (13-week T-bill) and an equal-weight blend of the four built bots. No parameter was tuned.",
        f"Window {qw.index[0].date()} to {qw.index[-1].date()} for every stream (the EDGAR event window), alpha vs QQQ.",
        "",
        "## Baseline: cash earns 0% (what a paper bot's `portfolio_worth` measures)",
        "",
        hdr,
        *t0,
        "",
        "## Lever 1: cash earns the T-bill yield (what the copied book holds in SHV)",
        "",
        hdr,
        *t1,
        "",
        "## Lever 2: equal-weight blend of the four built bots (us_sector_gate, country_gate, buyback_sp100, activist_13d)",
        "",
        hdr,
        row("blend, cash 0%", blend0, qw),
        row("blend, cash at T-bill", blend1, qw),
        "",
        "## Independence (the reason a blend can or cannot lift t)",
        "",
        "Correlation of daily returns:",
        "",
        md(corr),
        "",
        "Correlation of the QQQ-residuals (the alpha streams themselves):",
        "",
        md(resid),
        "",
    ]
    OUT.write_text("\n".join(text), encoding="utf-8")
    print("\n".join(text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
