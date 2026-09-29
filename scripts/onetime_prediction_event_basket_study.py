"""Phase C of prediction markets round 2: do Polymarket policy odds lead the ETFs they should move?

For macro releases, a prediction market is a thin copy of deeper markets (fed
funds futures, CPI fixings) and has nothing to add. Policy events are
different: there is no futures contract on "25% tariffs on Mexico by
February", so the Polymarket price is the only real-time read. If any
prediction-market signal carries information a daily bot can still use, it
should be here.

Hand-curated links (never auto-discovered): event -> (long ETFs, short ETFs,
sign). `sign=+1` means a rising probability should push the long leg above the
short leg. Three kinds:
- the 2024 election (Trump win: regional banks + energy vs solar/clean energy);
- single tariff threats (Mexico/Canada 25%, China 100%): SPY vs the country ETF;
- trade-deal markets per country (2025): the country ETF vs SPY;
- China tariff-rate bucket markets: the bucket-implied expected rate, SPY vs FXI.

Timing: Polymarket's daily point is stamped 00:00 UTC, i.e. the evening of the
prior Eastern day (after the 16:00 close), so it covers that day
(prediction_market_capture.covered_date). For trading day D:
  dx_D     = x at D's evening - x at the previous trading day's evening
  same-day = pair return close(D-1) -> close(D)   the mapping check: does it move?
  next-day = close(D) -> close(D+1)               the signal: dx_D is known before it
  5-day    = close(D) -> close(D+5)
dx is z-scored per link (scale only). Pooled slope of pair return on z(dx),
standard errors clustered by calendar week (every link moves on the same news
days) and, as a check, by event.

Pass: next-day t >= 2 in both halves (split at the median date), with the
more conservative clustering, and a positive same-day slope. Otherwise this is
a discretionary monitor, not a bot.

Usage:
    PYTHONPATH=. uv run python scripts/onetime_prediction_event_basket_study.py
    PYTHONPATH=. uv run python scripts/onetime_prediction_event_basket_study.py --search "tariff"
Polymarket pulls are cached under $RESEARCH_CACHE (default /tmp).

Results: docs/backtests/prediction-markets-2026-09.md
"""

import argparse
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tradingbot.utils import polymarket as pm
from tradingbot.utils.prediction_market_capture import covered_date

CACHE = os.environ.get("RESEARCH_CACHE", "/tmp")


@dataclass(frozen=True)
class Link:
    event: str  # Polymarket event slug
    market: str | None  # groupItemTitle of the market; None = the event's only market; "*rate*" = expected rate
    long: tuple[str, ...]
    short: tuple[str, ...]
    sign: int
    note: str


COUNTRY_ETF = {
    "China": "FXI",
    "India": "INDA",
    "European Union": "EZU",
    "Japan": "EWJ",
    "Canada": "EWC",
    "Mexico": "EWW",
    "South Korea": "EWY",
    "Vietnam": "VNM",
    "Australia": "EWA",
    "United Kingdom": "EWU",
    "Germany": "EWG",
    "France": "EWQ",
    "Brazil": "EWZ",
    "Argentina": "ARGT",
    "Israel": "EIS",
    "Taiwan": "EWT",
    "Switzerland": "EWL",
}
DEAL_EVENTS = (
    "which-countries-will-the-us-agree-to-trade-deals-with-before-july",
    "which-countries-will-the-us-agree-to-tariff-agreements-with-before-august",
    "which-countries-will-the-us-agree-to-tariff-agreements-with-in-august",
    "which-countries-will-the-us-agree-to-tariff-agreements-with-in-september",
    "which-countries-will-the-us-agree-to-tariff-agreements-with-in-october",
    "which-countries-will-the-us-agree-to-tariff-agreements-with-by-november-30",
    "which-countries-will-the-us-agree-to-tariff-agreements-with-by-december-31",
    "which-countries-will-the-us-agree-to-new-trade-deals-with-in-2025",
)
RATE_EVENTS = (
    "us-tariff-rate-on-china-on-august-15",
    "us-tariff-rate-on-china-on-november-12-2025",
    "us-tariff-rate-on-china-on-december-31-2026",
)
LINKS = [
    Link("presidential-election-winner-2024", "Donald Trump", ("KRE", "XLE"), ("TAN", "ICLN"), 1, "Trump trade"),
    Link("will-trump-impose-25-tariff-on-mexicocanada", None, ("SPY",), ("EWW", "EWC"), 1, "Mexico/Canada 25%"),
    Link("100-tariff-on-china-in-effect-by-november-1", None, ("SPY",), ("FXI",), 1, "China 100%"),
    *[Link(e, "*rate*", ("SPY",), ("FXI",), 1, "China tariff rate") for e in RATE_EVENTS],
    *[Link(e, c, (etf,), ("SPY",), 1, f"deal: {c}") for e in DEAL_EVENTS for c, etf in COUNTRY_ETF.items()],
]


def _cached(name: str, fetch):
    path = os.path.join(CACHE, name)
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    value = fetch()
    with open(path, "w") as fh:
        json.dump(value, fh)
    return value


def _bucket_mid(label: str) -> float | None:
    """'25-40%' -> 32.5, '<25%' -> 12.5, '>150%' -> 175."""
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", label or "")]
    if not nums:
        return None
    if label.strip().startswith("<"):
        return nums[0] / 2
    if label.strip().startswith(">"):
        return nums[0] * 7 / 6
    return sum(nums[:2]) / min(len(nums), 2)


def _history(client: pm.PolymarketClient, market: dict) -> pd.Series:
    token = pm.yes_token(market)
    if token is None:
        return pd.Series(dtype=float)
    points = _cached(f"polymarket_{market['slug']}.json", lambda: client.price_history(token))
    by_day = {}
    for p in points:
        if p.get("p") is None:
            continue
        observed = datetime.fromtimestamp(int(p["t"]), UTC).replace(tzinfo=None)
        by_day[pd.Timestamp(covered_date(observed))] = float(p["p"])
    return pd.Series(by_day, dtype=float).sort_index()


def link_series(client: pm.PolymarketClient, link: Link) -> pd.Series:
    """The link's daily x (probability, or expected tariff rate in %), by covered Eastern day."""
    try:
        event = _cached(f"polymarket_event_{link.event}.json", lambda: client.event(link.event))
    except LookupError:
        return pd.Series(dtype=float)
    markets = event.get("markets") or []
    if link.market == "*rate*":
        probs, mids = {}, {}
        for m in markets:
            mid = _bucket_mid(m.get("groupItemTitle") or "")
            if mid is not None:
                probs[m["slug"]], mids[m["slug"]] = _history(client, m), mid
        frame = pd.DataFrame(probs).dropna(how="all")
        if frame.empty:
            return pd.Series(dtype=float)
        frame = frame.ffill().fillna(0.0)
        total = frame.sum(axis=1)
        return (frame.mul(pd.Series(mids)).sum(axis=1) / total).where(total > 0).dropna()
    if link.market is None:
        chosen = markets[:1]
    else:
        chosen = [m for m in markets if (m.get("groupItemTitle") or "").strip() == link.market]
    return _history(client, chosen[0]) if chosen else pd.Series(dtype=float)


def _closes(symbols: set[str]) -> pd.DataFrame:
    raw = yf.download(sorted(symbols), start="2023-12-01", auto_adjust=True, progress=False)["Close"]
    raw.index = pd.to_datetime(raw.index).tz_localize(None).normalize()
    return raw


def observations(links: list[Link]) -> pd.DataFrame:
    closes = _closes({s for x in links for s in (*x.long, *x.short)} | {"SPY"})
    sessions = closes.index
    rows = []
    with pm.PolymarketClient() as client:
        for link in links:
            x = link_series(client, link)
            if len(x) < 10:
                continue
            legs = closes[list(link.long)].pct_change().mean(axis=1) - closes[list(link.short)].pct_change().mean(
                axis=1
            )
            fwd = {
                "same": legs,
                "next": legs.shift(-1),
                "5d": (
                    closes[list(link.long)].pct_change(5).mean(axis=1)
                    - closes[list(link.short)].pct_change(5).mean(axis=1)
                ).shift(-5),
            }
            # x as of each session's evening; weekend moves roll into Monday
            on_sessions = x.reindex(x.index.union(sessions)).ffill().reindex(sessions)
            on_sessions = on_sessions.loc[x.index.min() : x.index.max()]
            dx = on_sessions.diff().dropna()
            if dx.std() == 0 or len(dx) < 10:
                continue
            frame = pd.DataFrame(
                {
                    "day": dx.index,
                    "link": f"{link.event}|{link.market}",
                    "event": link.event,
                    "note": link.note,
                    "dx": dx.values,
                    "z": (dx / dx.std()).values,
                    **{k: (link.sign * v.reindex(dx.index)).values for k, v in fwd.items()},
                }
            )
            rows.append(frame)
    out = pd.concat(rows, ignore_index=True)
    out["week"] = out["day"].dt.to_period("W").astype(str)
    return out


def slope(frame: pd.DataFrame, y: str, cluster: str) -> dict:
    d = frame.dropna(subset=[y, "z"])
    if len(d) < 10:
        return {"n": len(d), "beta_bp": np.nan, "t": np.nan}
    zz = float((d["z"] ** 2).sum())
    beta = float((d["z"] * d[y]).sum() / zz)
    resid = d[y] - beta * d["z"]
    scores = (d["z"] * resid).groupby(d[cluster]).sum()
    g = len(scores)
    se = math.sqrt(float((scores**2).sum()) * g / max(g - 1, 1)) / zz
    return {"n": len(d), "clusters": g, "beta_bp": beta * 1e4, "t": beta / se if se > 0 else np.nan}


def report(obs: pd.DataFrame) -> bool:
    print(
        f"{len(obs)} link-days over {obs['link'].nunique()} links, {obs['day'].min().date()} -> {obs['day'].max().date()}"
    )
    print("\n## Mapping check: same-day slope per link group (bp of pair return per 1 sd of dx)")
    groups = obs["note"].str.replace(r"^deal: .*", "deals (all countries)", regex=True)
    table = pd.DataFrame({k: slope(g, "same", "week") for k, g in obs.groupby(groups)}).T
    print(table.round(2).to_string())
    split = obs["day"].sort_values().iloc[len(obs) // 2]
    print(f"\n## Pooled (split {split.date()}); t = min over week / event clustering")
    verdict = {}
    for part, g in (("full", obs), ("H1", obs[obs["day"] < split]), ("H2", obs[obs["day"] >= split])):
        for y in ("same", "next", "5d"):
            by_week, by_event = slope(g, y, "week"), slope(g, y, "event")
            t = min(by_week["t"], by_event["t"], key=lambda v: abs(v) if np.isfinite(v) else np.inf)
            verdict[(part, y)] = t
            print(
                f"{part:4} {y:4}  n {by_week['n']:5}  beta {by_week['beta_bp']:+7.2f} bp  "
                f"t week {by_week['t']:+.2f}  t event {by_event['t']:+.2f}  -> {t:+.2f}"
            )
    big = obs[obs["z"].abs() > 1]
    print(f"\n## Big moves only (|z| > 1, {len(big)} link-days): next-day, sign rule")
    rule = np.sign(big["z"]) * big["next"]
    weekly = rule.groupby(big["week"]).sum()
    t_rule = float(weekly.mean() / weekly.std() * math.sqrt(len(weekly))) if len(weekly) > 2 else np.nan
    print(f"mean {rule.mean() * 1e4:+.1f} bp per link-day, t (weekly) {t_rule:+.2f}")
    passes = verdict[("full", "same")] > 0 and verdict[("H1", "next")] >= 2 and verdict[("H2", "next")] >= 2
    print(f"\nVERDICT: {'PASSES -> build PredictionMarketEventBot' if passes else 'does not pass (monitor only)'}")
    return passes


def search(query: str) -> None:
    """Candidate events for a human to curate; nothing here feeds the study automatically."""
    with pm.PolymarketClient() as client:
        payload = client.get(
            f"{pm.GAMMA_URL}/public-search", {"q": query, "limit_per_type": 25, "events_status": "all"}
        )
    for e in payload.get("events", []):
        vol = float(e.get("volume") or 0) / 1e6
        print(f"{vol:8.1f}M  {e.get('slug')}  {str(e.get('startDate'))[:10]} -> {str(e.get('endDate'))[:10]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--search", help="print candidate Polymarket events for curation and exit")
    args = parser.parse_args()
    if args.search:
        search(args.search)
        return 0
    obs = observations(LINKS)
    obs.to_csv(os.path.join(CACHE, "prediction_event_basket_obs.csv"), index=False)
    report(obs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
