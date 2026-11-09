"""
Vectorised event-book machinery shared by the broad buyback study and the 13D study (2026-11).

An event book holds each name for ``hold`` sessions after its entry close, equal weight across whatever is
active that day, ``cost`` per side on the entry and exit session, flat days = 0. Alpha and t are the
CLAUDE.md formula against QQQ (daily).

Entry rule (no look-ahead): the first session close strictly after the filing's acceptance time, with the
close taken as 16:00 America/New_York converted to UTC (DST-aware; the older placebo script used a fixed
21:00 UTC, which for a 16:00-17:00 EDT filing enters on a close that already happened before the filing).
Early closes (13:00) are ignored; they are a handful of days a year.
"""

from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

BROAD_DIR = Path(".cache/broad")
PRICE_CACHE = BROAD_DIR / "prices.pkl"


def load_prices(symbols: list[str], start: str = "2009-01-01", chunk: int = 150) -> dict[str, pd.Series]:
    """Adjusted daily closes (cached). Symbols with fewer than 300 bars are dropped."""
    want = sorted(set(symbols) | {"QQQ"})
    cached: dict[str, pd.Series] = {}
    if PRICE_CACHE.exists():
        cached = pickle.loads(PRICE_CACHE.read_bytes())
    todo = [s for s in want if s not in cached]
    for i in range(0, len(todo), chunk):
        part = todo[i : i + chunk]
        df = yf.download(part, start=start, auto_adjust=True, progress=False, threads=True)["Close"]
        if isinstance(df, pd.Series):
            df = df.to_frame(part[0])
        for s in part:
            if s in df.columns and df[s].notna().sum() > 0:
                cached[s] = df[s].dropna().sort_index()
            else:
                cached[s] = pd.Series(dtype=float)  # remember the miss so reruns do not refetch
    BROAD_DIR.mkdir(parents=True, exist_ok=True)
    PRICE_CACHE.write_bytes(pickle.dumps(cached))
    return {s: cached[s] for s in want if len(cached.get(s, [])) > 300}


def build_matrix(prices: dict[str, pd.Series]):
    """(symbols, index, R, q): R[d, j] daily return of symbol j on day d; q = QQQ daily return."""
    syms = sorted(s for s in prices if s != "QQQ")
    frames = {}
    for s in syms:
        p = prices[s]
        p = p[~p.index.duplicated(keep="last")]
        frames[s] = p
    df = pd.DataFrame(frames).sort_index()
    df.index = (
        pd.DatetimeIndex(df.index).tz_localize(None).normalize()
        if df.index.tz is None
        else df.index.tz_convert(None).normalize()
    )
    qqq = prices["QQQ"]
    qqq = qqq[~qqq.index.duplicated(keep="last")].sort_index()
    qqq.index = (
        pd.DatetimeIndex(qqq.index).tz_localize(None).normalize()
        if qqq.index.tz is None
        else qqq.index.tz_convert(None).normalize()
    )
    idx = df.index.union(qqq.index)
    df = df.reindex(idx)
    R = df.pct_change(fill_method=None).to_numpy(dtype=float)
    q = qqq.reindex(idx).pct_change(fill_method=None).to_numpy(dtype=float)
    return syms, idx, R, q


def session_close_utc(idx: pd.DatetimeIndex) -> np.ndarray:
    """16:00 New York on each date, as naive UTC datetime64."""
    local = (idx + pd.Timedelta(hours=16)).tz_localize("America/New_York", ambiguous="NaT", nonexistent="NaT")
    return local.tz_convert("UTC").tz_localize(None).to_numpy()


def entry_positions(idx: pd.DatetimeIndex, acceptance_utc: pd.Series) -> np.ndarray:
    """Index position of the first close strictly after each acceptance time (len(idx) if none)."""
    closes = session_close_utc(idx)
    acc = pd.to_datetime(acceptance_utc, utc=True).dt.tz_convert("UTC").dt.tz_localize(None).to_numpy()
    return np.searchsorted(closes, acc, side="right")


def book_returns(entry_idx: np.ndarray, sym_idx: np.ndarray, hold: int, R: np.ndarray, cost: float) -> np.ndarray:
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


def alpha_t(p: np.ndarray, q: np.ndarray) -> tuple[float, float, float]:
    """(t, annualised alpha as a fraction, beta) of p on q."""
    m = np.isfinite(q) & np.isfinite(p)
    p, q = p[m], q[m]
    if len(p) < 30 or np.var(q, ddof=1) <= 0:
        return float("nan"), float("nan"), float("nan")
    beta = np.cov(p, q)[0, 1] / np.var(q, ddof=1)
    resid = p - beta * q
    sd = resid.std(ddof=1)
    if sd <= 0:
        return 0.0, 0.0, float(beta)
    return float(resid.mean() / sd * np.sqrt(len(resid))), float(resid.mean() * 252), float(beta)


def evaluate_book(entry: np.ndarray, sidx: np.ndarray, hold: int, R: np.ndarray, q: np.ndarray, cost: float, lo: int):
    """Full-window and half-window stats from index position ``lo`` (first usable day) onward."""
    p = book_returns(entry, sidx, hold, R, cost)
    sl = slice(max(lo, 1), None)
    t, a, b = alpha_t(p[sl], q[sl])
    mid = (max(lo, 1) + len(p)) // 2
    t1, a1, _ = alpha_t(p[max(lo, 1) : mid], q[max(lo, 1) : mid])
    t2, a2, _ = alpha_t(p[mid:], q[mid:])
    return {"t": t, "alpha": a * 100, "beta": b, "t1": t1, "a1": a1 * 100, "t2": t2, "a2": a2 * 100}


def passes(res: dict) -> bool:
    return bool(np.isfinite(res["t"]) and res["t"] >= 2 and res["a1"] > 0 and res["a2"] > 0 and res["beta"] < 0.5)


def placebo(
    entry: np.ndarray,
    sidx: np.ndarray,
    hold: int,
    R: np.ndarray,
    q: np.ndarray,
    cost: float,
    lo: int,
    observed_t: float,
    draws: int = 3000,
    seed: int = 7,
    same_name: bool = True,
    universe_cols: np.ndarray | None = None,
) -> tuple[float, float, float]:
    """(p, mean t, 95th pct t) of random books with the same event count.

    same_name=True keeps each event's symbol and draws a random date in [lo, end - hold - 1], so the
    placebo has the same names (and so the same survivorship / size / beta mix) and only the timing is
    random. same_name=False also redraws the symbol from ``universe_cols``.
    """
    rng = np.random.default_rng(seed)
    n = R.shape[0]
    ts = np.empty(draws)
    hi = n - hold - 2
    for i in range(draws):
        e = rng.integers(max(lo, 1), hi, size=len(entry))
        s = sidx if same_name else rng.choice(universe_cols, size=len(entry))
        pr = book_returns(e, s, hold, R, cost)
        ts[i], _, _ = alpha_t(pr[max(lo, 1) :], q[max(lo, 1) :])
    ts = ts[np.isfinite(ts)]
    return float((ts >= observed_t).mean()), float(ts.mean()), float(np.percentile(ts, 95))


def dedupe(events: pd.DataFrame, days: int = 30) -> pd.DataFrame:
    """Keep the first event per symbol, then any later one at least ``days`` days after the last kept."""
    df = events.sort_values("acceptance_dt").reset_index(drop=True)
    keep, last = [], {}
    for i, r in df.iterrows():
        s, ts = r["symbol"], pd.Timestamp(r["acceptance_dt"])
        if s not in last or (ts - last[s]).total_seconds() >= days * 86400:
            keep.append(i)
            last[s] = ts
    return df.loc[keep].reset_index(drop=True)


def event_cars(entry: np.ndarray, sidx: np.ndarray, hold: int, R: np.ndarray, q: np.ndarray) -> np.ndarray:
    """Per-event cumulative log return over the hold minus QQQ's, NaN if fewer than hold-3 valid days."""
    out = np.full(len(entry), np.nan)
    for i, (e, j) in enumerate(zip(entry, sidx, strict=True)):
        r = R[e + 1 : e + 1 + hold, j]
        b = q[e + 1 : e + 1 + hold]
        m = np.isfinite(r) & np.isfinite(b)
        if m.sum() >= hold - 3:
            out[i] = float(np.sum(np.log1p(r[m]) - np.log1p(b[m])))
    return out


def car_summary(cars: np.ndarray) -> dict:
    """Event-level mean / median / 5%-winsorised mean (percent), win rate and t of the mean."""
    c = cars[np.isfinite(cars)] * 100
    if len(c) < 5:
        return {"n": len(c)}
    lo, hi = np.percentile(c, [5, 95])
    return {
        "n": len(c),
        "mean": float(c.mean()),
        "median": float(np.median(c)),
        "wmean": float(np.clip(c, lo, hi).mean()),
        "win": float((c > 0).mean() * 100),
        "t": float(c.mean() / c.std(ddof=1) * np.sqrt(len(c))),
    }


def control_car(sidx: np.ndarray, hold: int, R: np.ndarray, q: np.ndarray, lo: int, draws: int = 20, seed: int = 3):
    """Mean event-level CAR (percent) of same-name random-date draws: (mean, sd across draws)."""
    rng = np.random.default_rng(seed)
    hi = R.shape[0] - hold - 2
    means = []
    for _ in range(draws):
        e = rng.integers(max(lo, 1), hi, size=len(sidx))
        c = event_cars(e, sidx, hold, R, q)
        means.append(float(np.nanmean(c) * 100))
    return float(np.mean(means)), float(np.std(means))


def car_row(label: str, entry: np.ndarray, sidx: np.ndarray, hold: int, R: np.ndarray, q: np.ndarray, lo: int) -> str:
    s = car_summary(event_cars(entry, sidx, hold, R, q))
    if "mean" not in s:
        return f"| {label} | {hold}d | {s['n']} | n/a | | | | | |"
    cm, csd = control_car(sidx, hold, R, q, lo)
    return (
        f"| {label} | {hold}d | {s['n']} | {s['mean']:+.2f}% | {s['median']:+.2f}% | {s['wmean']:+.2f}% | "
        f"{s['win']:.0f}% | {s['t']:.2f} | {cm:+.2f}% ({csd:.2f}) |"
    )


CAR_HEADER = (
    "| Events | Hold | N | Mean CAR vs QQQ | Median | 5%-winsorised mean | Win rate | t (mean) | Same-name random-date mean (sd) |\n"
    "|---|---|---|---|---|---|---|---|---|"
)


def placebo_same_dates(
    entry: np.ndarray,
    hold: int,
    R: np.ndarray,
    q: np.ndarray,
    cost: float,
    lo: int,
    observed_t: float,
    universe_cols: np.ndarray,
    draws: int = 3000,
    seed: int = 11,
) -> tuple[float, float, float]:
    """(p, mean t, 95th pct t) of books that keep the real event DATES but redraw the names from ``universe_cols``.

    This isolates timing: if filings cluster around market moments, a book built on those dates earns "alpha" vs QQQ
    from when it is invested, with no stock-specific drift. A random-date placebo cannot see that.
    """
    rng = np.random.default_rng(seed)
    ts = np.empty(draws)
    for i in range(draws):
        s = rng.choice(universe_cols, size=len(entry))
        pr = book_returns(entry, s, hold, R, cost)
        ts[i], _, _ = alpha_t(pr[max(lo, 1) :], q[max(lo, 1) :])
    ts = ts[np.isfinite(ts)]
    return float((ts >= observed_t).mean()), float(ts.mean()), float(np.percentile(ts, 95))
