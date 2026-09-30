"""Late-day S&P ranges: do Kalshi's quotes lag the index on the settlement day itself?

Round 2, idea #2. Kill test B and the executable-edge test entered 23 hours
before settlement, priced with yesterday's VIX1D. That is where a retail crowd
is least wrong. On the settlement day the outcome narrows by the hour; if
quotes lag the index, the edge is largest in the last hour, and fees
(0.07 * p * (1 - p)) are smallest exactly there, near 0 and 1. KXINX trades
until 16:00 ET, the close it settles on.

Data:
- KXINX daily ranges settling at 16:00 ET, on days with hourly ^GSPC / ^VIX1D
  bars (yfinance keeps 730 days: 2023-10 onward).
- Kalshi 1-minute candles on the settlement day. The quote at entry time T is
  the last candle ending at or before T, and no older than 15 minutes; its
  yes_bid / yes_ask close.
- S&P and VIX1D at T: the close of the hourly bar ending at T (bars run
  9:30-10:30 ... 14:30-15:30 ET). Entries: 10:30, 12:30, 14:30, 15:30 ET.

Fair value: driftless lognormal over the minutes left to 16:00 at k * VIX1D.
The scale k is fitted on the first half of the days (Brier) and applied to
the second half.

Trades, one $1 contract each, at the quote, net of the taker fee:
buy YES at the ask when fair - ask - fee > m, buy NO at 1 - bid when
bid - fair - fee > m. P&L t-stats clustered by day.

Pass: H2 net P&L t >= 2 at some entry time and margin, and positive in H1.

Usage:
    PYTHONPATH=. uv run python scripts/onetime_prediction_late_day_spx.py [--max-days N]
Kalshi pulls are cached under $RESEARCH_CACHE (default /tmp).

Results: docs/backtests/prediction-markets-2026-09.md
"""

import argparse
import math
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import onetime_prediction_market_calibration as cal

from tradingbot.utils import kalshi

CACHE = cal.CACHE
EASTERN = ZoneInfo("America/New_York")
ENTRIES = ("10:30", "12:30", "14:30", "15:30")
QUOTE_MAX_AGE = 15 * 60
BAND_SIGMA = 4.0  # ranges within this many prior-close daily sigmas of the prior close
MARGINS = (0.02, 0.05, 0.10)
K_GRID = np.round(np.arange(0.5, 2.01, 0.1), 2)


def _hourly(symbol: str) -> pd.Series:
    """Close at each bar's END, indexed in Eastern time."""
    raw = yf.download(symbol, period="730d", interval="1h", auto_adjust=False, progress=False)["Close"]
    if isinstance(raw, pd.DataFrame):
        raw = raw.iloc[:, 0]
    idx = pd.to_datetime(raw.index).tz_convert(EASTERN)
    ends = [min(t + timedelta(hours=1), t.replace(hour=16, minute=0)) for t in idx]
    return pd.Series(raw.values, index=pd.DatetimeIndex(ends)).dropna()


def _quote(kc: kalshi.KalshiClient, market: dict, day: pd.Timestamp) -> list[dict]:
    lo = int(datetime(day.year, day.month, day.day, 10, 10, tzinfo=EASTERN).timestamp())
    hi = int(datetime(day.year, day.month, day.day, 15, 31, tzinfo=EASTERN).timestamp())

    def fetch():
        try:
            return kc.candles(cal.SERIES, market, lo, hi, period_minutes=1)
        except kalshi.KalshiNotFound:
            return []

    return cal._cached(f"kalshi_1m_{market['ticker']}_{lo}.json", fetch)


def collect(max_days: int | None) -> pd.DataFrame:
    spx, vix1d = _hourly("^GSPC"), _hourly("^VIX1D")
    daily = spx.groupby(spx.index.date).last()  # 16:00 closes
    first = spx.index.min().date()
    rows = []
    with kalshi.KalshiClient() as kc:
        markets = cal._cached("kalshi_kxinx_markets.json", lambda: kc.list_markets(cal.SERIES))
        events: dict[str, list[dict]] = {}
        for m in markets:
            if m.get("result") in ("yes", "no"):
                events.setdefault(m["event_ticker"], []).append(m)
        days_done = 0
        for event, group in sorted(events.items()):
            close = kalshi.market_window(group[0])[1]
            if close is None:
                continue
            close_et = close.replace(tzinfo=ZoneInfo("UTC")).astimezone(EASTERN)
            if (close_et.hour, close_et.minute) != (16, 0) or close_et.date() <= first:
                continue
            day = pd.Timestamp(close_et.date())
            prior = [d for d in daily.index if d < day.date()]
            if not prior or day.date() not in daily.index:
                continue
            s_prev = float(daily.loc[prior[-1]])
            v_prev = vix1d[vix1d.index.date == prior[-1]]
            if v_prev.empty:
                continue
            band = s_prev * float(v_prev.iloc[-1]) / 100 / math.sqrt(252)
            if max_days and days_done >= max_days:
                break
            days_done += 1
            for m in group:
                bounds = cal._range(m)
                if bounds is None:
                    continue
                lo, hi = bounds
                mid = (lo + hi) / 2 if math.isfinite(lo) and math.isfinite(hi) else (lo if math.isfinite(lo) else hi)
                if abs(mid - s_prev) > BAND_SIGMA * band:
                    continue
                candles = sorted(_quote(kc, m, day), key=lambda c: c["end_period_ts"])
                for entry in ENTRIES:
                    hh, mm = map(int, entry.split(":"))
                    t = datetime(day.year, day.month, day.day, hh, mm, tzinfo=EASTERN)
                    ts = int(t.timestamp())
                    past = [c for c in candles if ts - QUOTE_MAX_AGE <= c["end_period_ts"] <= ts]
                    spot, vol = spx.get(pd.Timestamp(t)), vix1d.get(pd.Timestamp(t))
                    if not past or spot is None or vol is None:
                        continue
                    q = kalshi.parse_candle(past[-1])
                    rows.append(
                        {
                            "day": day,
                            "event": event,
                            "ticker": m["ticker"],
                            "entry": entry,
                            "lo": lo,
                            "hi": hi,
                            "spot": float(spot),
                            "vix1d": float(vol),
                            "minutes_left": (16 - hh) * 60 - mm,
                            "bid": q["bid"],
                            "ask": q["ask"],
                            "y": 1.0 if m["result"] == "yes" else 0.0,
                        }
                    )
            if days_done % 25 == 0:
                print(f"{days_done} days, {len(rows)} quotes", flush=True)
    return pd.DataFrame(rows)


def fair(frame: pd.DataFrame, k: float) -> np.ndarray:
    t = frame["minutes_left"].to_numpy() / 390 / 252
    sigma = k * frame["vix1d"].to_numpy() / 100
    return np.array(
        [
            cal.lognormal_prob(s, lo, hi, sg, tt)
            for s, lo, hi, sg, tt in zip(frame["spot"], frame["lo"], frame["hi"], sigma, t, strict=True)
        ]
    )


def pnl(frame: pd.DataFrame, p: np.ndarray, margin: float) -> pd.DataFrame:
    bid, ask, y = frame["bid"].fillna(0.0), frame["ask"].fillna(1.0), frame["y"]
    buy = (ask > 0) & (ask < 1) & (p - ask - cal.fee(ask) > margin)
    sell = (bid > 0) & (bid < 1) & (bid - p - cal.fee(bid) > margin)
    out = frame[buy | sell].copy()
    out["pnl"] = np.where(buy[buy | sell], (y - ask - cal.fee(ask))[buy | sell], (bid - y - cal.fee(bid))[buy | sell])
    return out


def _t(g: pd.DataFrame) -> dict:
    by_day = g.groupby("day")["pnl"].sum()
    t = float(by_day.mean() / by_day.std() * math.sqrt(len(by_day))) if len(by_day) > 2 else np.nan
    return {
        "trades": len(g),
        "days": len(by_day),
        "c_per_trade": float(g["pnl"].mean() * 100) if len(g) else np.nan,
        "t_day": t,
    }


def report(frame: pd.DataFrame) -> None:
    frame = frame.sort_values("day").reset_index(drop=True)
    days = frame["day"].drop_duplicates().sort_values()
    split = days.iloc[len(days) // 2]
    h1, h2 = frame[frame["day"] < split], frame[frame["day"] >= split]
    print(
        f"{len(frame)} quotes on {len(days)} days, {days.iloc[0].date()} -> {days.iloc[-1].date()}, split {split.date()}"
    )
    quoted = frame[(frame["bid"] > 0) & (frame["ask"] > 0) & (frame["ask"] < 1)]
    print(
        "two-sided share by entry:",
        (quoted.groupby("entry").size() / frame.groupby("entry").size()).round(2).to_dict(),
        "median spread:",
        (quoted["ask"] - quoted["bid"]).groupby(quoted["entry"]).median().round(3).to_dict(),
    )
    # fit k on H1 per entry time (Brier), apply to both halves
    ks = {}
    for entry, g in h1.groupby("entry"):
        scores = {k: float(((fair(g, k) - g["y"]) ** 2).mean()) for k in K_GRID}
        ks[entry] = min(scores, key=scores.get)
    print("k (VIX1D scale) fitted on H1:", ks)
    print("\n## Calibration, H2 (Brier): model vs Kalshi mid, per entry time")
    for entry, g in h2.groupby("entry"):
        q = g[(g["bid"] > 0) & (g["ask"] > 0) & (g["ask"] < 1)]
        pm_mid = (q["bid"] + q["ask"]) / 2
        print(
            f"{entry}: n {len(q)}  model {((fair(q, ks[entry]) - q['y']) ** 2).mean():.4f}  "
            f"kalshi mid {((pm_mid - q['y']) ** 2).mean():.4f}"
        )
    print("\n## Net taker P&L (cents per $1 contract), k from H1; t clustered by day")
    rows = []
    for entry in ENTRIES:
        for margin in MARGINS:
            for part, g in (("H1", h1), ("H2", h2)):
                g = g[g["entry"] == entry]
                if g.empty:
                    continue
                rows.append({"entry": entry, "margin": margin, "half": part, **_t(pnl(g, fair(g, ks[entry]), margin))})
    table = pd.DataFrame(rows)
    print(table.round(3).to_string(index=False))
    best = table[table["half"] == "H2"].sort_values("t_day", ascending=False).head(1)
    if not best.empty:
        e, m = best.iloc[0]["entry"], best.iloc[0]["margin"]
        h1_row = table[(table["entry"] == e) & (table["margin"] == m) & (table["half"] == "H1")].iloc[0]
        ok = best.iloc[0]["t_day"] >= 2 and h1_row["c_per_trade"] > 0
        print(
            f"\nBest H2 cell: {e} margin {m}: H2 t {best.iloc[0]['t_day']:.2f}, "
            f"H1 {h1_row['c_per_trade']:+.2f}c/trade -> {'PASSES' if ok else 'does not pass'}"
        )
        print("(picking the best H2 cell is itself a selection; treat a pass as a lead for a demo run.)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-days", type=int, default=None)
    args = parser.parse_args()
    frame = collect(args.max_days)
    if frame.empty:
        print("no quotes collected")
        return 1
    frame.to_csv(os.path.join(CACHE, "prediction_late_day_spx.csv"), index=False)
    report(frame)
    return 0


if __name__ == "__main__":
    sys.exit(main())
