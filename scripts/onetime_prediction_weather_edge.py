"""Kalshi daily high temperature: does a public weather model beat the crowd?

Round 2, idea #4: a niche where information, not speed, could be the edge.
Kalshi lists 2°F brackets on each city's daily high as reported by the NWS
climate report (CLI) for one station. A forecast model is free and public; if
the crowd prices the brackets worse than a bias-corrected model does, buying
the underpriced bracket pays after fees.

Model (point-in-time throughout):
- Forecast F = the day's max of the hourly 2 m temperature at the station,
  from Open-Meteo's archive of past model runs (previous-runs API): GFS from
  2021, averaged with ECMWF IFS where it exists (2024+).
- The climate day is local STANDARD time midnight to midnight (NWS CLI).
- Two entries, each using only runs published before it:
  - "eve": 22:00 ET the day before, with `previous_day2` (runs >= 48h old);
  - "morning": 10:00 ET on the day, with `previous_day1` (runs >= 24h old).
- Realized = the winning bracket (interval-censored). Bias mu and spread
  sigma of (realized - F) are fitted per city and entry by censored
  maximum likelihood on the trailing 365 days, refitted monthly on days
  settled before the month began.
- P(bracket) = Phi((hi - F - mu) / sigma) - Phi((lo - F - mu) / sigma), on the
  continuous bounds of an integer reading ("63 to 64" = (62.5, 64.5]).

Trades: one $1 contract at the entry candle's quote (hourly candle ending at
the entry), net of the taker fee 0.07 * p * (1 - p) (all KXHIGH* series are
`quadratic`, multiplier 1): buy YES at the ask when p - ask - fee > m; buy NO
at 1 - bid when bid - p - fee > m. P&L t-stats clustered by date (all
cities share one day's weather news).

Pass: net t >= 2 in both halves (split at the median date) at some margin.

Usage:
    PYTHONPATH=. uv run python scripts/onetime_prediction_weather_edge.py [--cities NY,CHI] [--workers 6]
Kalshi and Open-Meteo pulls are cached under $RESEARCH_CACHE (default /tmp).

Results: docs/backtests/prediction-markets-2026-09.md
"""

import argparse
import math
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import norm

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import onetime_prediction_market_calibration as cal

from tradingbot.utils import kalshi

CACHE = cal.CACHE
EASTERN = ZoneInfo("America/New_York")
PREVIOUS_RUNS = "https://previous-runs-api.open-meteo.com/v1/forecast"
MARGINS = (0.02, 0.05, 0.10)
FIT_DAYS = 365
MIN_FIT = 60


@dataclass(frozen=True)
class City:
    key: str
    series: str
    lat: float
    lon: float
    lst_offset: int  # hours from UTC, local standard time (the NWS climate day)


CITIES = {
    "NY": City("NY", "KXHIGHNY", 40.7789, -73.9692, -5),  # Central Park
    "CHI": City("CHI", "KXHIGHCHI", 41.7868, -87.7522, -6),  # Midway
    "MIA": City("MIA", "KXHIGHMIA", 25.7959, -80.2870, -5),  # Miami Intl
    "AUS": City("AUS", "KXHIGHAUS", 30.1945, -97.6699, -6),  # Austin-Bergstrom
    "DEN": City("DEN", "KXHIGHDEN", 39.8561, -104.6737, -7),  # Denver Intl
    "PHIL": City("PHIL", "KXHIGHPHIL", 39.8683, -75.2311, -5),  # Philadelphia Intl
    "LAX": City("LAX", "KXHIGHLAX", 33.9382, -118.3866, -8),  # Los Angeles Intl
}
ENTRIES = {"eve": ("previous_day2", -1, 22), "morning": ("previous_day1", 0, 10)}  # (run age, day offset, ET hour)


def bracket(market: dict) -> tuple[float, float] | None:
    """Continuous bounds of the bracket from its wording: '63° to 64°', '71° or above', '62° or below'."""
    text = market.get("yes_sub_title") or market.get("subtitle") or market.get("title") or ""
    nums = [int(x) for x in re.findall(r"-?\d+", text)]
    if not nums:
        return None
    low = text.lower()
    if " to " in low and len(nums) >= 2:
        return nums[0] - 0.5, nums[1] + 0.5
    if re.search(r"above|higher|more", low):
        return nums[0] - 0.5, math.inf
    if re.search(r"below|lower|less", low):
        return -math.inf, nums[0] + 0.5
    return None


def _event_day(event_ticker: str) -> date | None:
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})$", event_ticker)
    if not m:
        return None
    return datetime.strptime(f"{m.group(1)}{m.group(2)}{m.group(3)}", "%y%b%d").date()


def forecasts(city: City, start: date, end: date) -> pd.DataFrame:
    """Daily forecast max per climate day for previous_day1 / previous_day2, GFS and ECMWF."""
    frames = []
    for y0 in range(start.year, end.year + 1):
        a, b = max(start, date(y0, 1, 1)), min(end, date(y0, 12, 31))
        for model in ("gfs_seamless", "ecmwf_ifs025"):

            def fetch(a=a, b=b, model=model):
                r = httpx.get(
                    PREVIOUS_RUNS,
                    params={
                        "latitude": city.lat,
                        "longitude": city.lon,
                        "hourly": "temperature_2m_previous_day1,temperature_2m_previous_day2",
                        "temperature_unit": "fahrenheit",
                        "timezone": "UTC",
                        "start_date": a.isoformat(),
                        "end_date": b.isoformat(),
                        "models": model,
                    },
                    timeout=120,
                )
                if r.status_code == 400:  # model not archived for this range (ECMWF before 2024)
                    return {}
                r.raise_for_status()
                return r.json().get("hourly", {})

            h = cal._cached(f"openmeteo_{city.key}_{model}_{a}_{b}.json", fetch)
            if not h:
                continue
            f = pd.DataFrame(h)
            f["time"] = pd.to_datetime(f["time"]) + timedelta(hours=city.lst_offset)  # local standard time
            f["day"] = f["time"].dt.normalize()
            daily = f.groupby("day")[["temperature_2m_previous_day1", "temperature_2m_previous_day2"]].max(min_count=20)
            daily.columns = [f"{model}_d1", f"{model}_d2"]
            frames.append(daily)
    out = pd.concat(frames).apply(pd.to_numeric).groupby(level=0).first()
    for d in ("d1", "d2"):
        out[f"f_{d}"] = out[[f"gfs_seamless_{d}", f"ecmwf_ifs025_{d}"]].mean(axis=1, skipna=True)
    return out


def _quotes(city: City, market: dict, kc: kalshi.KalshiClient) -> list[dict]:
    open_t, close_t = kalshi.market_window(market)
    if open_t is None or close_t is None:
        return []
    lo = int(open_t.replace(tzinfo=ZoneInfo("UTC")).timestamp())
    hi = int(close_t.replace(tzinfo=ZoneInfo("UTC")).timestamp())

    def fetch():
        try:
            return kc.candles(city.series, market, lo, hi, period_minutes=60)
        except kalshi.KalshiNotFound:
            return []

    return cal._cached(f"kalshi_1h_{market['ticker']}.json", fetch)


def collect_city(city: City, workers: int) -> pd.DataFrame:
    with kalshi.KalshiClient(retries=8, backoff=3.0) as kc:
        markets = cal._cached(f"kalshi_{city.series}_markets.json", lambda: kc.list_markets(city.series))
    markets = [m for m in markets if m.get("result") in ("yes", "no")]
    days = [d for d in (_event_day(m["event_ticker"]) for m in markets) if d]
    fc = forecasts(city, min(days) - timedelta(days=3), max(days))

    def one(chunk):
        rows = []
        with kalshi.KalshiClient(retries=8, backoff=3.0) as kc:
            for m in chunk:
                day, b = _event_day(m["event_ticker"]), bracket(m)
                if day is None or b is None:
                    continue
                candles = sorted(_quotes(city, m, kc), key=lambda c: c["end_period_ts"])
                for name, (_run, offset, hour) in ENTRIES.items():
                    d = day + timedelta(days=offset)
                    ts = int(datetime(d.year, d.month, d.day, hour, tzinfo=EASTERN).timestamp())
                    past = [c for c in candles if ts - 3 * 3600 <= c["end_period_ts"] <= ts]
                    if not past:
                        continue
                    q = kalshi.parse_candle(past[-1])
                    rows.append(
                        {
                            "city": city.key,
                            "day": pd.Timestamp(day),
                            "event": m["event_ticker"],
                            "ticker": m["ticker"],
                            "entry": name,
                            "lo": b[0],
                            "hi": b[1],
                            "bid": q["bid"],
                            "ask": q["ask"],
                            "y": 1.0 if m["result"] == "yes" else 0.0,
                        }
                    )
        return rows

    chunks = [markets[i::workers] for i in range(workers)]
    with ThreadPoolExecutor(workers) as pool:
        rows = [r for part in pool.map(one, chunks) for r in part]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["f"] = [
        fc.loc[d, "f_d2" if e == "eve" else "f_d1"] if d in fc.index else np.nan
        for d, e in zip(frame["day"], frame["entry"], strict=True)
    ]
    print(f"{city.key}: {len(frame)} quotes, {frame['day'].nunique()} days", flush=True)
    return frame


def _fit(realized: pd.DataFrame) -> tuple[float, float]:
    """Censored MLE of (mu, sigma) for realized - F from winning-bracket intervals."""
    lo = (realized["lo"] - realized["f"]).to_numpy()
    hi = (realized["hi"] - realized["f"]).to_numpy()

    def nll(x):
        mu, s = x[0], math.exp(x[1])
        p = norm.cdf((hi - mu) / s) - norm.cdf((lo - mu) / s)
        return -np.log(np.clip(p, 1e-9, 1)).sum()

    res = minimize(nll, x0=[0.0, math.log(3.0)], method="Nelder-Mead")
    return float(res.x[0]), float(math.exp(res.x[1]))


def model_probs(frame: pd.DataFrame) -> pd.Series:
    """P(bracket) with (mu, sigma) refit monthly on the trailing year of settled days before the month."""
    out = pd.Series(np.nan, index=frame.index)
    winners = frame[frame["y"] == 1].drop_duplicates(["city", "entry", "day"]).dropna(subset=["f"])
    for (city, entry), g in frame.dropna(subset=["f"]).groupby(["city", "entry"]):
        w = winners[(winners["city"] == city) & (winners["entry"] == entry)]
        for month, gm in g.groupby(g["day"].dt.to_period("M")):
            start = month.start_time
            past = w[(w["day"] < start) & (w["day"] >= start - timedelta(days=FIT_DAYS))]
            if len(past) < MIN_FIT:
                continue
            mu, s = _fit(past)
            z_hi = (gm["hi"] - gm["f"] - mu) / s
            z_lo = (gm["lo"] - gm["f"] - mu) / s
            out.loc[gm.index] = norm.cdf(z_hi) - norm.cdf(z_lo)
    return out


def pnl(frame: pd.DataFrame, margin: float) -> pd.DataFrame:
    bid, ask, y, p = frame["bid"].fillna(0.0), frame["ask"].fillna(1.0), frame["y"], frame["p"]
    buy = (ask > 0) & (ask < 1) & (p - ask - cal.fee(ask) > margin)
    sell = (bid > 0) & (bid < 1) & (bid - p - cal.fee(bid) > margin)
    out = frame[buy | sell].copy()
    out["pnl"] = np.where(buy[buy | sell], (y - ask - cal.fee(ask))[buy | sell], (bid - y - cal.fee(bid))[buy | sell])
    return out


def _t(g: pd.DataFrame) -> dict:
    by_day = g.groupby("day")["pnl"].sum()
    return {
        "trades": len(g),
        "days": len(by_day),
        "c_per_trade": float(g["pnl"].mean() * 100) if len(g) else np.nan,
        "t_day": float(by_day.mean() / by_day.std() * math.sqrt(len(by_day))) if len(by_day) > 2 else np.nan,
    }


def report(frame: pd.DataFrame) -> None:
    frame = frame.dropna(subset=["p"]).copy()
    split = frame["day"].drop_duplicates().sort_values().iloc[frame["day"].nunique() // 2]
    print(
        f"\n{len(frame)} priced quotes, {frame['day'].min().date()} -> {frame['day'].max().date()}, split {split.date()}"
    )
    q = frame[(frame["bid"] > 0) & (frame["ask"] > 0) & (frame["ask"] < 1)]
    print("\n## Brier on two-sided quotes: model vs Kalshi mid")
    for (entry, half), g in q.groupby(["entry", q["day"] >= split]):
        mid = (g["bid"] + g["ask"]) / 2
        print(
            f"{entry:8} {'H2' if half else 'H1'}  n {len(g):6}  model {((g['p'] - g['y']) ** 2).mean():.4f}  "
            f"kalshi {((mid - g['y']) ** 2).mean():.4f}  spread {float((g['ask'] - g['bid']).median()):.3f}"
        )
    print("\n## Net taker P&L (cents per $1 contract), t clustered by date")
    rows = []
    for entry in ENTRIES:
        for margin in MARGINS:
            for half, g in (("H1", frame[frame["day"] < split]), ("H2", frame[frame["day"] >= split])):
                rows.append({"entry": entry, "margin": margin, "half": half, **_t(pnl(g[g["entry"] == entry], margin))})
    table = pd.DataFrame(rows)
    print(table.round(3).to_string(index=False))
    both = table.pivot_table(index=["entry", "margin"], columns="half", values="t_day")
    passing = both[(both["H1"] >= 2) & (both["H2"] >= 2)]
    print(f"\nVERDICT: {'PASSES: ' + str(passing.index.tolist()) if len(passing) else 'does not pass'}")
    print("\n## By city (morning entry, margin 0.05)")
    g = frame[frame["entry"] == "morning"]
    print(pd.DataFrame({c: _t(pnl(x, 0.05)) for c, x in g.groupby("city")}).T.round(3).to_string())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cities", default=",".join(CITIES))
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    frames = [collect_city(CITIES[c], args.workers) for c in args.cities.split(",")]
    frame = pd.concat([f for f in frames if not f.empty], ignore_index=True)
    frame["p"] = model_probs(frame)
    frame.to_csv(os.path.join(CACHE, "prediction_weather_edge.csv"), index=False)
    report(frame)
    return 0


if __name__ == "__main__":
    sys.exit(main())
