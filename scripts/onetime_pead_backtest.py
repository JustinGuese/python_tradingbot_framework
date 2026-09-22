"""Post-earnings-announcement drift (PEAD) sleeve: research backtest.

After an EPS beat, go long at the close of the first full session after the
announcement, hold `hold` sessions, keep at most `max_names`, equal weight, rest
cash. Score vs QQQ (alpha) AND vs an equal-weight hold of the same universe:
today's mega caps are survivors, so only the second isolates the timing edge.

Usage: onetime_pead_backtest.py [hold_sessions] [max_names] [min_surprise_pct]
Results: docs/backtests/pead-2026-09.md
"""

import os
import sys
import time
import warnings

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")
# Cache dir for downloads (fg.json, per-symbol earnings). Kept out of the repo.
S = os.environ.get("RESEARCH_CACHE", "/tmp")

UNIVERSE = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOGL",
    "META",
    "AVGO",
    "TSLA",
    "BRK-B",
    "JPM",
    "LLY",
    "V",
    "UNH",
    "XOM",
    "MA",
    "JNJ",
    "PG",
    "HD",
    "COST",
    "ABBV",
    "MRK",
    "CVX",
    "KO",
    "PEP",
    "ADBE",
    "CRM",
    "WMT",
    "BAC",
    "NFLX",
    "TMO",
    "MCD",
    "CSCO",
    "ACN",
    "ABT",
    "LIN",
    "ORCL",
    "AMD",
    "DHR",
    "INTC",
    "WFC",
    "TXN",
    "PM",
    "NEE",
    "DIS",
    "VZ",
    "CMCSA",
    "INTU",
    "QCOM",
    "AMGN",
    "IBM",
    "HON",
    "UNP",
    "CAT",
    "GS",
    "LOW",
    "SPGI",
    "BA",
    "RTX",
    "PFE",
    "ELV",
    "AMAT",
    "ISRG",
    "SBUX",
    "MS",
    "BLK",
    "DE",
    "GE",
    "PLD",
    "MDT",
    "GILD",
    "ADP",
    "BKNG",
    "LMT",
    "SYK",
    "C",
    "MDLZ",
    "ADI",
    "TJX",
    "MMC",
    "REGN",
    "VRTX",
    "CVS",
    "MO",
    "SO",
    "ZTS",
    "CB",
    "NOC",
    "PGR",
    "DG",
    "WM",
    "TEAM",
    "KDP",
    "PYPL",
]


def earnings(sym: str) -> pd.DataFrame:
    cache = f"{S}/pead_earn_{sym}.json"
    if os.path.exists(cache):
        out = pd.read_json(cache)
        out["when"] = pd.to_datetime(out["when"], unit="s", utc=True)
        return out
    for attempt in range(3):
        try:
            df = yf.Ticker(sym).get_earnings_dates(limit=60)
            break
        except Exception:
            time.sleep(2 * (attempt + 1))
    else:
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    col = next((c for c in df.columns if "surprise" in c.lower()), None)
    if col is None:
        return pd.DataFrame()
    out = pd.DataFrame(
        {"when": pd.to_datetime(df.index, utc=True), "surprise": pd.to_numeric(df[col].to_numpy(), errors="coerce")}
    )
    out = out.dropna().reset_index(drop=True)
    out.to_json(cache, date_unit="s")
    return out


def main():
    hold = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    max_names = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    min_surprise = float(sys.argv[3]) if len(sys.argv) > 3 else 5.0

    px = yf.download([*UNIVERSE, "QQQ"], start="2014-01-01", auto_adjust=True, progress=False)["Close"]
    px.index = pd.DatetimeIndex(px.index).tz_localize(None).normalize()
    rets = px.pct_change()
    sessions = px.index

    events = []
    for sym in UNIVERSE:
        e = earnings(sym)
        if e.empty or sym not in px:
            continue
        for _, r in e.iterrows():
            # Announcements come before the open or after the close; the time in
            # the timestamp says which. Enter at the close of the first session
            # that fully follows the news: the same day if it came pre-open
            # (before 12:00 ET), else the next session.
            et = pd.Timestamp(r["when"]).tz_convert("America/New_York")
            day = et.tz_localize(None).normalize()
            after = sessions[sessions >= day] if et.hour < 12 else sessions[sessions > day]
            if len(after) == 0:
                continue
            events.append({"sym": sym, "entry": after[0], "surprise": float(r["surprise"])})
    ev = pd.DataFrame(events)
    print("events:", len(ev), "symbols with data:", ev.sym.nunique(), "from", ev.entry.min().date())

    # Build the daily book: a position is live from entry+1 through entry+hold.
    book = pd.DataFrame(0.0, index=sessions, columns=UNIVERSE)
    active: list[tuple[int, int, str, float]] = []  # (start_i, end_i, sym, surprise)
    ev = ev[ev.surprise >= min_surprise].sort_values("entry")
    by_day = dict(list(ev.groupby("entry")))
    for i, d in enumerate(sessions):
        active = [a for a in active if a[1] >= i]
        if d in by_day:
            for _, r in by_day[d].sort_values("surprise", ascending=False).iterrows():
                if any(a[2] == r.sym for a in active):
                    continue
                active.append((i + 1, i + hold, r.sym, r.surprise))
        live = [a for a in active if a[0] <= i + 1 <= a[1]]
        # Cap by largest surprise; hold equal weight of the survivors next session.
        live = sorted(live, key=lambda a: -a[3])[:max_names]
        if i + 1 < len(sessions) and live:
            for a in live:
                book.iloc[i + 1, book.columns.get_loc(a[2])] = 1.0 / max_names

    turnover = book.diff().abs().sum(axis=1).fillna(0)
    strat = (book * rets[UNIVERSE].fillna(0)).sum(axis=1) - turnover * 0.0005
    ew = rets[UNIVERSE].mean(axis=1)
    q = rets["QQQ"]
    df = pd.DataFrame({"s": strat, "ew": ew, "q": q}).dropna()
    df = df[df.index >= "2016-01-01"]

    def st(x, bench):
        b = x.cov(bench) / bench.var()
        res = x - b * bench
        eq = (1 + x).cumprod()
        return (
            f"cagr={eq.iloc[-1] ** (252 / len(x)) - 1:6.1%} beta={b:4.2f} alpha={res.mean() * 252:6.1%} "
            f"t={res.mean() / res.std() * np.sqrt(len(res)):5.2f} dd={(eq / eq.cummax() - 1).min():6.1%}"
        )

    print(
        f"params hold={hold} max_names={max_names} min_surprise={min_surprise}%  avg gross={book.sum(axis=1).mean():.0%}"
    )
    for name, (lo, hi) in {
        "H1 2016-20": ("2016", "2020-12-31"),
        "H2 2021-26": ("2021", "2027"),
        "FULL": ("2016", "2027"),
    }.items():
        x = df.loc[lo:hi]
        print(
            f"  {name:11s} vs QQQ: {st(x.s, x.q)} | vs EW-universe: {st(x.s, x.ew)} | EW-universe vs QQQ: {st(x.ew, x.q)}"
        )


if __name__ == "__main__":
    main()
