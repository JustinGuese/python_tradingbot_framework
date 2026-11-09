"""
Does the QuantsmdSmaDailyGatedBot mechanism work outside the US sector ETFs? (2026-11)

The bot (docs/backtests/quantsmd-v3-2026-11.md) holds a sector when SMA50 > SMA200, equal weight capped at
25%, and goes to cash while SPY <= its SMA200. Its only 25-year sample is the US. This runs the same rule,
untuned (50 / 200 / 0.25 / off_scale 0), on three universes:

  us_sectors      XLB XLE XLF XLI XLK XLP XLU XLV XLY      replication check against the documented t ~3.1
  global_sectors  iShares Global sector ETFs (10)          different companies, 2001+
  countries       iShares country ETFs (20)                different markets, 1996+

and, per universe, three books: ungated, gated by the universe's own equal-weight index, gated by SPY.
That is 9 runs, pre-listed here; nothing is tuned. A universe "generalises" when a gated book clears the
project bar (alpha vs QQQ t >= 2, positive in both halves, beta < 0.5) AND the gate adds return over the
ungated book of the same universe (gate-effect alpha positive in both halves).

Simulator: vectorised, signals at close t earn the t -> t+1 return, cost = 5 bp x |change in weight|, cash
earns 0. It is a re-implementation of the bot's rule, validated by the us_sectors row.

Usage: POSTGRES_URI=postgresql://x:x@localhost:5432/x PYTHONPATH=. python scripts/onetime_gate_other_markets.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from tradingbot.utils.alpha_report import alpha_stats

FAST, SLOW, CAP, GATE_WINDOW = 50, 200, 0.25, 200
COST = 0.0005
MIN_MEMBERS = 6

UNIVERSES: dict[str, list[str]] = {
    "us_sectors": ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"],
    "global_sectors": ["IXC", "IXJ", "IXN", "IXG", "IXP", "MXI", "EXI", "RXI", "KXI", "JXI"],
    "countries": [
        "EWJ", "EWG", "EWU", "EWC", "EWA", "EWH", "EWS", "EWY", "EWT", "EWZ",
        "EWW", "EWP", "EWQ", "EWI", "EWL", "EWD", "EWN", "EWK", "EWM", "EZA",
    ],
}  # fmt: skip
OUT = Path("docs/backtests/gate-other-markets-2026-11.md")


def load_prices() -> pd.DataFrame:
    syms = sorted({s for u in UNIVERSES.values() for s in u} | {"SPY", "QQQ"})
    df = yf.download(syms, start="1996-01-01", auto_adjust=True, progress=False)["Close"]
    df = df.sort_index()
    df.index = pd.DatetimeIndex(df.index).tz_localize(None).normalize()
    return df


def ew_index(closes: pd.DataFrame) -> pd.Series:
    """Equal-weight index of the members available each day (level, starts at 100)."""
    r = closes.pct_change(fill_method=None)
    return (1 + r.mean(axis=1).fillna(0.0)).cumprod() * 100


def book_weights(closes: pd.DataFrame, gate: pd.Series | None) -> pd.DataFrame:
    sma_f = closes.rolling(FAST, min_periods=FAST).mean()
    sma_s = closes.rolling(SLOW, min_periods=SLOW).mean()
    active = (sma_f > sma_s) & sma_f.notna() & sma_s.notna()
    n = active.sum(axis=1).replace(0, np.nan)
    w = active.astype(float).mul((1.0 / n).clip(upper=CAP), axis=0).fillna(0.0)
    if gate is not None:
        w = w.mul(gate.reindex(w.index).fillna(1.0), axis=0)  # fails open before the gate has history
    return w


def gate_series(ref: pd.Series) -> pd.Series:
    sma = ref.rolling(GATE_WINDOW, min_periods=GATE_WINDOW).mean()
    return (ref > sma).astype(float).where(sma.notna(), 1.0)


def simulate(closes: pd.DataFrame, w: pd.DataFrame, cost: float = COST) -> pd.Series:
    r = closes.pct_change(fill_method=None).fillna(0.0)
    held = w.shift(1).fillna(0.0)  # decided at close t-1, earns return on day t
    gross = (held * r).sum(axis=1)
    turnover = held.diff().abs().sum(axis=1).fillna(0.0)
    return gross - cost * turnover


def max_dd(r: pd.Series) -> float:
    lv = (1 + r).cumprod()
    return float((lv / lv.cummax() - 1).min())


def stats(r: pd.Series, qqq: pd.Series) -> dict:
    a = alpha_stats(r, qqq)
    if a is None:
        return {}
    yrs = len(r) / 252
    return {
        "alpha": a.alpha * 100, "t": a.t, "beta": a.beta, "corr": a.corr, "n": a.n,
        "cagr": ((1 + r).prod() ** (1 / yrs) - 1) * 100 if yrs > 0 else float("nan"),
        "dd": max_dd(r) * 100,
    }  # fmt: skip


def halves(r: pd.Series, qqq: pd.Series):
    mid = r.index[len(r) // 2]
    return stats(r[r.index < mid], qqq), stats(r[r.index >= mid], qqq)


def main() -> int:
    px = load_prices()
    qqq = px["QQQ"].pct_change(fill_method=None).dropna()
    spy_gate = gate_series(px["SPY"].dropna())
    rows, sub_rows, eff_rows = [], [], []
    notes = []

    for uname, members in UNIVERSES.items():
        have = [m for m in members if m in px.columns and px[m].notna().sum() > 400]
        missing = sorted(set(members) - set(have))
        if missing:
            notes.append(f"{uname}: no usable data for {', '.join(missing)}")
        closes = px[have].dropna(how="all")
        own_gate = gate_series(ew_index(closes))
        valid_count = closes.rolling(SLOW, min_periods=SLOW).mean().notna().sum(axis=1)
        start = valid_count[valid_count >= MIN_MEMBERS].index[0]
        books = {
            "ungated": book_weights(closes, None),
            "gated_own": book_weights(closes, own_gate),
            "gated_spy": book_weights(closes, spy_gate),
        }
        rets = {k: simulate(closes, w).loc[start:] for k, w in books.items()}
        for k, r in rets.items():
            r_costly = simulate(closes, books[k], cost=0.002).loc[start:]
            s = stats(r, qqq)
            if not s:
                continue
            h1, h2 = halves(r, qqq)
            s20 = stats(r_costly, qqq)
            ok = bool(s["t"] >= 2 and h1 and h2 and h1["alpha"] > 0 and h2["alpha"] > 0 and s["beta"] < 0.5)
            rows.append(
                f"| {uname} | {k} | {r.index[0].date()} | {s['alpha']:+.1f}% | {s['t']:.2f} | {s['beta']:.2f} | "
                f"{s['corr']:.2f} | {s['cagr']:.1f}% | {s['dd']:.0f}% | "
                f"{h1['alpha']:+.1f}% ({h1['t']:.2f}) | {h2['alpha']:+.1f}% ({h2['t']:.2f}) | "
                f"{s20['alpha']:+.1f}% ({s20['t']:.2f}) | {'PASS' if ok else 'fail'} |"
            )
            for lo, hi in [("1996", "2006"), ("2007", "2012"), ("2013", "2019"), ("2020", "2026")]:
                seg = r.loc[lo:hi]
                if len(seg) > 250:
                    ss = stats(seg, qqq)
                    if ss:
                        sub_rows.append(
                            f"| {uname} | {k} | {lo}-{hi} | {ss['alpha']:+.1f}% | {ss['t']:.2f} | {ss['beta']:.2f} |"
                        )
        for g in ("gated_own", "gated_spy"):
            diff = rets[g] - rets["ungated"]
            s = stats(diff, qqq)
            h1, h2 = halves(diff, qqq)
            if s and h1 and h2:
                eff_rows.append(
                    f"| {uname} | {g} - ungated | {s['alpha']:+.1f}% | {s['t']:.2f} | "
                    f"{h1['alpha']:+.1f}% ({h1['t']:.2f}) | {h2['alpha']:+.1f}% ({h2['t']:.2f}) | "
                    f"{'both +' if h1['alpha'] > 0 and h2['alpha'] > 0 else 'not both +'} |"
                )

    hdr = (
        "| Universe | Book | Start | Alpha/yr | t | Beta | Corr | CAGR | Max DD | H1 alpha (t) | H2 alpha (t) "
        "| 20 bp alpha (t) | Bar |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    )
    text = [
        "# Does the trend gate work outside US sector ETFs? (generated)",
        "",
        "Generated by `scripts/onetime_gate_other_markets.py`; interpretation is added by hand below the tables.",
        "Rule is the one in `QuantsmdSmaDailyGatedBot`, untuned (SMA 50/200, cap 25%, gate window 200, off = cash),",
        "5 bp per unit of turnover (20 bp column for a harsher check), cash earns 0. Alpha is vs QQQ.",
        "'Start' is the first day six or more members have a full SMA200. QQQ begins 1999-03, so the alpha window",
        "starts there for every row (earlier days have no benchmark).",
        "",
        "## Books vs QQQ (the project bar: t >= 2, both halves positive, beta < 0.5)",
        "",
        hdr,
        *rows,
        "",
        "## What the gate adds (gated minus ungated book of the same universe, regressed on QQQ)",
        "",
        "| Universe | Difference | Alpha/yr | t | H1 alpha (t) | H2 alpha (t) | Halves |",
        "|---|---|---|---|---|---|---|",
        *eff_rows,
        "",
        "## Sub-periods (alpha vs QQQ)",
        "",
        "| Universe | Book | Period | Alpha/yr | t | Beta |",
        "|---|---|---|---|---|---|",
        *sub_rows,
        "",
    ]
    if notes:
        text += ["Data notes: " + "; ".join(notes), ""]
    OUT.write_text("\n".join(text), encoding="utf-8")
    print("\n".join(text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
