"""Kill test B: are Kalshi's S&P 500 range prices better calibrated than the options market's?

If the prediction market were better calibrated than options on some slice, there
could be a threshold-gap trade: buy a call spread wherever Kalshi's odds are
above the options market's. If options are at least as good everywhere, that
trade does not exist, because the Kalshi side cannot be traded from here
(US-only), so only the options side is available, and it is the one that is
right.

Data (checked 2026-09-28):
- Kalshi's only S&P 500 contracts are **daily**: KXINX range ladders (legacy
  INX-/INXD- events, 2022-04+), each listed ~24h before the 16:00 ET close it
  settles on. There are no weekly or monthly series with markets in them.
- The free SPY chain history (DoltHub, in option_quotes) has only 14/28/43-DTE
  expiries, so no 1-day digital can be priced from real chains.

So the options benchmark here is a **proxy**: a lognormal range probability at
the options market's own short-dated implied vol, ^VIX1D (from 2023-04) or
^VIX9D (earlier), as of the prior close. It has no skew. Skew moves mass into
the left tail, which a flat-vol model misprices; that should show up in the
far-OTM slices, and the doc treats those accordingly.

Timing: Kalshi price = the first hourly candle ending at least one hour after
the prior day's 16:00 ET close, when both S_{D-1} and the VIX closes are known.
Horizon = trading days from D-1 to D, over 252.

Output: Brier score and log loss per source, sliced by year, moneyness (range
midpoint distance from spot in sigma) and market volume; calibration buckets.

--executable (Phase A of round 2): instead of scoring last-trade prices, trade
at the entry candle's quotes and settle at the result, net of Kalshi's fee:
- taker: buy NO at 1 - yes_bid / buy YES at yes_ask, when the options proxy
  disagrees with the quote by more than the fee plus a margin;
- tilt: sell every YES bid in [0.30, 0.55], no options model at all (the
  favourite-longshot overpricing the calibration run found);
- maker (UPPER BOUND): rest one tick inside the spread and count a fill if any
  later hourly candle before the close trades through the quote. Hourly
  candles show no queue position, so this overstates maker fills.
Fees: KXINX is `fee_type=quadratic`, multiplier 1 (series API, 2026-09-29):
taker 0.07 * p * (1 - p) per contract, no maker fee. Legacy INX paid 0.035;
the current, higher rate is used throughout. Per-order round-up to the cent
is ignored (negligible at 100+ contracts).
P&L t-stats are clustered by event (one ladder per settlement day).

Usage:
    PYTHONPATH=. uv run python scripts/onetime_prediction_market_calibration.py [--max-events N] [--executable]
Kalshi pulls are cached under $RESEARCH_CACHE (default /tmp).

Results: docs/backtests/prediction-markets-2026-09.md
"""

import argparse
import json
import math
import os
import sys
import threading
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf
from scipy.stats import norm

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tradingbot.utils import kalshi

CACHE = os.environ.get("RESEARCH_CACHE", "/tmp")
EASTERN = ZoneInfo("America/New_York")
SERIES = "KXINX"
BAND_SIGMA = 3.0  # only ranges whose midpoint is within this many sigmas of spot


def _cached(name: str, fetch):
    path = os.path.join(CACHE, name)
    if os.path.exists(path):
        try:
            with open(path) as fh:
                return json.load(fh)
        except json.JSONDecodeError:  # a run killed mid-write; fetch again
            pass
    value = fetch()
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w") as fh:
        json.dump(value, fh)
    os.replace(tmp, path)  # atomic: a killed run never leaves a half-written file
    return value


def _closes(symbol: str) -> pd.Series:
    raw = yf.download(symbol, start="2021-01-01", auto_adjust=False, progress=False)
    close = raw["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
    return close.dropna()


def _range(market: dict) -> tuple[float, float] | None:
    kind, strike, cap, _ = kalshi.market_strike(market)
    if kind == "range" and strike is not None and cap is not None:
        return strike, cap
    if kind == "range" and strike is not None:  # legacy "-B4125": a 25-point range centred there
        return strike - 12.5, strike + 12.5
    if kind == "close_above" and strike is not None:
        return strike, math.inf
    if kind == "close_below" and strike is not None:
        return -math.inf, strike
    return None


def lognormal_prob(s0: float, lo: float, hi: float, sigma: float, t: float) -> float:
    """P(lo < S_T <= hi) under driftless GBM."""

    def above(k):
        if k == -math.inf:
            return 1.0
        if k == math.inf:
            return 0.0
        d2 = (math.log(s0 / k) - 0.5 * sigma**2 * t) / (sigma * math.sqrt(t))
        return float(norm.cdf(d2))

    return above(lo) - above(hi)


def _entry_price(kc: kalshi.KalshiClient, market: dict, after: datetime, close: datetime) -> dict | None:
    """The first hourly candle ending at/after `after`, plus the trade range of every later candle."""
    start = int(after.replace(tzinfo=UTC).timestamp())
    end = int(close.replace(tzinfo=UTC).timestamp())

    def fetch():
        try:
            return kc.candles(SERIES, market, start - 3600, end, period_minutes=60)
        except kalshi.KalshiNotFound:
            return []

    candles = sorted(_cached(f"kalshi_{market['ticker']}_{start}_{end}.json", fetch), key=lambda c: c["end_period_ts"])
    for i, candle in enumerate(candles):
        if candle["end_period_ts"] >= start:
            parsed = kalshi.parse_candle(candle)
            if parsed["prob"] is not None:
                later = [c.get("price") or {} for c in candles[i + 1 :]]
                highs = [h for h in (kalshi._price_field(p, "high") for p in later) if h is not None]
                lows = [x for x in (kalshi._price_field(p, "low") for p in later) if x is not None]
                parsed["later_high"] = max(highs) if highs else np.nan
                parsed["later_low"] = min(lows) if lows else np.nan
                return parsed
    return None


def collect(max_events: int | None) -> pd.DataFrame:
    spx, vix1d, vix9d = _closes("^GSPC"), _closes("^VIX1D"), _closes("^VIX9D")
    with kalshi.KalshiClient() as kc:
        markets = _cached("kalshi_kxinx_markets.json", lambda: kc.list_markets(SERIES))
        events: dict[str, list[dict]] = {}
        for m in markets:
            if m.get("result") in ("yes", "no"):
                events.setdefault(m["event_ticker"], []).append(m)
        rows = []
        for n, (event, group) in enumerate(sorted(events.items())):
            if max_events and n >= max_events:
                break
            close = kalshi.market_window(group[0])[1]
            if close is None:
                continue
            close_et = close.replace(tzinfo=UTC).astimezone(EASTERN)
            if (close_et.hour, close_et.minute) != (16, 0):  # the close, not the intraday (H1000..H1500) events
                continue
            day = pd.Timestamp(close_et.date())
            prior = spx.index[spx.index < day]
            if prior.empty or day not in spx.index:
                continue
            d0 = prior[-1]
            s0 = float(spx.loc[d0])
            sigma = float(vix1d.get(d0, np.nan)) / 100
            vol_src = "VIX1D"
            if not np.isfinite(sigma):
                sigma, vol_src = float(vix9d.get(d0, np.nan)) / 100, "VIX9D"
            if not np.isfinite(sigma):
                continue
            t = max(1, len(spx.loc[d0:day]) - 1) / 252
            band = s0 * sigma * math.sqrt(t)
            # entry: one hour after the prior day's 16:00 ET close
            after = datetime(d0.year, d0.month, d0.day, 17, tzinfo=EASTERN).astimezone(UTC).replace(tzinfo=None)
            for m in group:
                bounds = _range(m)
                if bounds is None:
                    continue
                lo, hi = bounds
                mid = (lo + hi) / 2 if math.isfinite(lo) and math.isfinite(hi) else (lo if math.isfinite(lo) else hi)
                z = (mid - s0) / band
                if abs(z) > BAND_SIGMA:
                    continue
                entry = _entry_price(kc, m, after, close)
                if entry is None:
                    continue
                rows.append(
                    {
                        "event": event,
                        "day": day,
                        "ticker": m["ticker"],
                        "lo": lo,
                        "hi": hi,
                        "z": z,
                        "p_pm": entry["prob"],
                        "bid": entry["bid"],
                        "ask": entry["ask"],
                        "later_high": entry["later_high"],
                        "later_low": entry["later_low"],
                        "spread": (entry["ask"] - entry["bid"]) if entry["ask"] and entry["bid"] else np.nan,
                        "volume": kalshi._to_float(m.get("volume_fp") or m.get("volume")),
                        "p_opt": lognormal_prob(s0, lo, hi, sigma, t),
                        "vol_src": vol_src,
                        "y": 1.0 if m["result"] == "yes" else 0.0,
                    }
                )
            if n % 50 == 0:
                print(f"{n}/{len(events)} events, {len(rows)} markets", flush=True)
    return pd.DataFrame(rows)


def _scores(frame: pd.DataFrame) -> dict:
    eps = 1e-4
    out = {"n": len(frame)}
    for col in ("p_pm", "p_opt"):
        p = frame[col].clip(eps, 1 - eps)
        out[f"brier_{col}"] = float(((p - frame["y"]) ** 2).mean())
        out[f"logloss_{col}"] = float(-(frame["y"] * np.log(p) + (1 - frame["y"]) * np.log(1 - p)).mean())
    diff = (frame["p_pm"] - frame["y"]) ** 2 - (frame["p_opt"] - frame["y"]) ** 2
    # events are the independent unit: cluster the Brier difference by event
    by_event = diff.groupby(frame["event"]).mean()
    out["brier_diff_t"] = (
        float(by_event.mean() / by_event.std() * math.sqrt(len(by_event))) if len(by_event) > 2 else np.nan
    )
    return out


def report(frame: pd.DataFrame) -> None:
    frame = frame.copy()
    frame["year"] = frame["day"].dt.year
    frame["moneyness"] = pd.cut(frame["z"].abs(), [0, 0.5, 1, 2, BAND_SIGMA], include_lowest=True).astype(str)
    frame["vol_q"] = pd.qcut(frame["volume"].rank(method="first"), 3, labels=["low", "mid", "high"]).astype(str)
    print("\n## Overall (brier_diff_t < 0 means Kalshi is better, clustered by event)")
    print(pd.DataFrame([_scores(frame)]).round(4).to_string(index=False))
    for key in ("year", "moneyness", "vol_q", "vol_src"):
        table = pd.DataFrame({k: _scores(g) for k, g in frame.groupby(key)}).T
        print(f"\n## By {key}")
        print(table.round(4).to_string())
    print("\n## Calibration buckets (mean predicted vs realised)")
    for col in ("p_pm", "p_opt"):
        buckets = pd.cut(frame[col], [0, 0.05, 0.1, 0.2, 0.35, 0.5, 0.65, 0.8, 0.9, 0.95, 1.0], include_lowest=True)
        table = frame.groupby(buckets, observed=True).agg(n=("y", "size"), pred=(col, "mean"), realised=("y", "mean"))
        print(f"\n{col}\n{table.round(3).to_string()}")


TAKER_FEE = 0.07  # KXINX: quadratic, multiplier 1; maker fee 0
TICK = 0.01
TILT = (0.30, 0.55)


def fee(p) -> np.ndarray:
    p = np.asarray(p, dtype=float)
    return TAKER_FEE * p * (1 - p)


def trades(frame: pd.DataFrame, margin: float = 0.02) -> dict[str, pd.DataFrame]:
    """Per-contract P&L of each rule; rows are the contracts it traded (one contract each)."""
    f = frame[(frame["bid"] > 0) & (frame["ask"] > 0) & (frame["ask"] < 1) & (frame["ask"] >= frame["bid"])].copy()
    bid, ask, p, y = f["bid"], f["ask"], f["p_opt"], f["y"]
    out = {}

    def take(mask, pnl, name):
        g = f[mask].copy()
        g["pnl"] = pnl[mask]
        out[name] = g

    sell = bid - fee(bid) - p > margin
    buy = p - ask - fee(ask) > margin
    both = f[sell | buy].copy()
    both["pnl"] = np.where(sell[sell | buy], (bid - y - fee(bid))[sell | buy], (y - ask - fee(ask))[sell | buy])
    out[f"opt_taker_m{margin:.2f}"] = both
    take(sell, bid - y - fee(bid), f"opt_taker_sell_m{margin:.2f}")
    take(buy, y - ask - fee(ask), f"opt_taker_buy_m{margin:.2f}")
    take(bid.between(*TILT), bid - y - fee(bid), "tilt_taker")
    take(pd.Series(True, index=f.index), bid - y - fee(bid), "sell_every_yes_taker")

    # maker, UPPER BOUND: one tick inside the spread, filled if a later candle traded through it
    q_sell, q_buy = (ask - TICK).clip(lower=bid + TICK), (bid + TICK).clip(upper=ask - TICK)
    mk_sell = (q_sell - p > margin) & (q_sell < ask) & (f["later_high"] >= q_sell)
    mk_buy = (p - q_buy > margin) & (q_buy > bid) & (f["later_low"] <= q_buy)
    mk = f[mk_sell | mk_buy].copy()
    mk["pnl"] = np.where(mk_sell[mk_sell | mk_buy], (q_sell - y)[mk_sell | mk_buy], (y - q_buy)[mk_sell | mk_buy])
    out[f"opt_maker_ub_m{margin:.2f}"] = mk
    take((q_sell < ask) & q_sell.between(*TILT) & (f["later_high"] >= q_sell), q_sell - y, "tilt_maker_ub")
    return out


def _pnl_stats(g: pd.DataFrame) -> dict:
    by_event = g.groupby("event")["pnl"].sum()
    t = float(by_event.mean() / by_event.std() * math.sqrt(len(by_event))) if len(by_event) > 2 else np.nan
    return {
        "contracts": len(g),
        "events": len(by_event),
        "pnl_per_contract": float(g["pnl"].mean()) if len(g) else np.nan,
        "pnl_per_event": float(by_event.mean()) if len(by_event) else np.nan,
        "t_event": t,
    }


def executable_report(frame: pd.DataFrame) -> None:
    frame = frame.copy()
    frame["year"] = pd.to_datetime(frame["day"]).dt.year
    frame["moneyness"] = pd.cut(frame["z"].abs(), [0, 0.5, 1, 2, BAND_SIGMA], include_lowest=True).astype(str)
    quoted = frame[(frame["bid"] > 0) & (frame["ask"] > 0) & (frame["ask"] < 1)]
    print(f"\n{len(quoted)}/{len(frame)} contracts had a two-sided quote at entry")
    print(f"median spread {float((quoted['ask'] - quoted['bid']).median()):.3f}")
    ladder = quoted.groupby("event").agg(bids=("bid", "sum"), asks=("ask", "sum"), n=("bid", "size"))
    print(f"ladder sum of bids (median) {ladder['bids'].median():.3f}, of asks {ladder['asks'].median():.3f}")
    rules: dict[str, pd.DataFrame] = {}
    for margin in (0.0, 0.02, 0.05):
        rules.update(trades(frame, margin))
    print("\n## Net P&L per rule (dollars per $1 contract; t clustered by event)")
    print(pd.DataFrame({k: _pnl_stats(g) for k, g in rules.items()}).T.round(4).to_string())
    for key in ("year", "moneyness"):
        for name in ("opt_taker_m0.02", "tilt_taker", "opt_maker_ub_m0.02", "tilt_maker_ub"):
            g = rules[name]
            table = pd.DataFrame({k: _pnl_stats(x) for k, x in g.groupby(key)}).T
            print(f"\n## {name} by {key}")
            print(table.round(4).to_string())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-events", type=int, default=None)
    parser.add_argument("--executable", action="store_true", help="net P&L at quotes instead of Brier scores")
    args = parser.parse_args()
    frame = collect(args.max_events)
    if frame.empty:
        print("no markets collected")
        return 1
    frame.to_csv(os.path.join(CACHE, "prediction_market_calibration.csv"), index=False)
    if args.executable:
        executable_report(frame)
    else:
        report(frame)
    return 0


if __name__ == "__main__":
    sys.exit(main())
