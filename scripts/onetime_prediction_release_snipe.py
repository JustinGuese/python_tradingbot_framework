"""Release sniping: once an outcome is public, how long do prediction-market prices stay stale?

Round 2, idea #3. At a scheduled release the outcome becomes known at a known
second. Any contract still trading away from 0/1 after that is free money for
whoever is fastest.

Kalshi (checked 2026-09-29 against the API's close_time): **impossible by
design.** Every release market stops trading before its number is published:
- KXCPI closes at 08:25 ET;
- KXPAYROLLS / KXU3 close at 08:29 ET;
- KXFEDDECISION closes at 13:59 ET.
KXINX, the exception, trades until the 16:00 close it settles on; that is the
late-day study (onetime_prediction_late_day_spx.py).

Polymarket: markets stay open until the resolver closes them, ~2 hours after an
FOMC statement. Fed-decision events trade $60-660M each and have fees disabled
(`feesEnabled: false`). CPI and jobs events trade < $0.5M and are left out.

Method: every taker trade (data-api /trades, takerOnly) in each Fed-decision
event from 1h before to 1h after the 14:00:00 ET statement. Each outcome token
settles at v in {0, 1} (the event's final outcomePrices). A taker who bought at
p gains v - p per share and one who sold gains p - v. After 14:00 that gain is
what a sniper earns: someone left a stale order and a taker picked it off.
Reported per event and per delay bucket after 14:00:
- "sniped $": taker gains > 0 (the edge that was there to take);
- "late $": taker losses (takers who traded the wrong way after the news).

Caveats: trade timestamps are Polygon settlement times, a few seconds after the
match, so the first bucket is really "within ~2-5s of the statement". The
statement goes out at 14:00:00 ET; a live bot needs a parser on the Fed's
release feed and co-located order entry to be in that bucket.

Usage:
    PYTHONPATH=. uv run python scripts/onetime_prediction_release_snipe.py
Trades are cached under $RESEARCH_CACHE (default /tmp).

Results: docs/backtests/prediction-markets-2026-09.md
"""

import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tradingbot.utils import polymarket as pm

CACHE = os.environ.get("RESEARCH_CACHE", "/tmp")
EASTERN = ZoneInfo("America/New_York")
DATA_URL = "https://data-api.polymarket.com/trades"
PAGE = 10_000
FED_EVENTS = (
    "fed-interest-rates-september-2024",
    "fed-interest-rates-november-2024",
    "fed-interest-rates-december-2024",
    "fed-interest-rates-january-2025",
    "fed-decision-in-march",
    "fed-decision-in-may-2025",
    "fed-decision-in-june",
    "fed-decision-in-july",
    "fed-decision-in-september",
    "fed-decision-in-october",
    "fed-decision-in-december",
    "fed-decision-in-january",
    "fed-decision-in-march-885",
    "fed-decision-in-april",
    "fed-decision-in-june-825",
    "fed-decision-in-july-181",
    "fed-decision-in-september-762",
)
BUCKETS = [0, 2, 10, 60, 300, 3600]  # seconds after the statement
BUCKET_LABELS = ["0-2s", "2-10s", "10-60s", "1-5m", "5-60m"]


def _cached(name: str, fetch):
    path = os.path.join(CACHE, name)
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    value = fetch()
    with open(path, "w") as fh:
        json.dump(value, fh)
    return value


def _trades(client: httpx.Client, condition_id: str, since: int) -> list[dict]:
    """Taker trades of one market back to `since` (the API returns newest first)."""
    out, offset = [], 0
    while True:
        r = client.get(DATA_URL, params={"market": condition_id, "takerOnly": "true", "limit": PAGE, "offset": offset})
        r.raise_for_status()
        batch = r.json()
        out.extend(batch)
        if len(batch) < PAGE or min(t["timestamp"] for t in batch) < since:
            return out
        offset += PAGE


def _statement_ts(event: dict) -> int:
    """14:00:00 ET on the decision day (the event's end date)."""
    day = pd.Timestamp(event["endDate"][:10]).date()
    return int(datetime(day.year, day.month, day.day, 14, tzinfo=EASTERN).timestamp())


def event_trades(client: httpx.Client, gamma: pm.PolymarketClient, slug: str) -> tuple[pd.DataFrame, dict]:
    event = _cached(f"polymarket_event_{slug}.json", lambda: gamma.event(slug))
    t0 = _statement_ts(event)
    rows, winner = [], None
    for m in event.get("markets") or []:
        outcomes = pm._json_list(m.get("outcomes"))
        finals = [float(x) for x in pm._json_list(m.get("outcomePrices"))]
        value = dict(zip(outcomes, finals, strict=False))
        if value.get("Yes") == 1.0:
            winner = m.get("groupItemTitle")
        trades = _cached(
            f"polymarket_trades_{m['conditionId']}.json", lambda m=m: _trades(client, m["conditionId"], t0 - 3600)
        )
        for t in trades:
            if not (t0 - 3600 <= t["timestamp"] <= t0 + 3600) or t.get("outcome") not in value:
                continue
            v = value[t["outcome"]]
            gain = (v - t["price"]) if t["side"] == "BUY" else (t["price"] - v)
            rows.append(
                {
                    "event": slug,
                    "market": m.get("groupItemTitle"),
                    "outcome": t["outcome"],
                    "dt": t["timestamp"] - t0,
                    "price": t["price"],
                    "size": float(t["size"]),
                    "v": v,
                    "taker_gain": gain * float(t["size"]),
                }
            )
    return pd.DataFrame(rows), {"winner": winner, "t0": t0}


def _pre_prob(frame: pd.DataFrame, winner: str | None) -> float:
    """Winner's YES price just before the statement: size-weighted over the last 10 minutes."""
    w = frame[(frame["market"] == winner) & (frame["outcome"] == "Yes") & frame["dt"].between(-600, -1)]
    if w.empty:
        return float("nan")
    return float((w["price"] * w["size"]).sum() / w["size"].sum())


def main() -> int:
    per_event, all_rows = [], []
    with httpx.Client(timeout=120) as client, pm.PolymarketClient() as gamma:
        for slug in FED_EVENTS:
            frame, meta = event_trades(client, gamma, slug)
            if frame.empty:
                print(f"{slug}: no trades in the window")
                continue
            after = frame[frame["dt"] >= 0].copy()
            after["bucket"] = pd.cut(after["dt"], BUCKETS, labels=BUCKET_LABELS, right=False)
            sniped = after[after["taker_gain"] > 0].groupby("bucket", observed=False)["taker_gain"].sum()
            late = -after[after["taker_gain"] < 0]["taker_gain"].sum()
            per_event.append(
                {
                    "event": slug,
                    "day": pd.Timestamp(meta["t0"], unit="s").date(),
                    "winner": meta["winner"],
                    "p_winner_pre": _pre_prob(frame, meta["winner"]),
                    "volume_1h_after": float((after["size"] * after["price"]).sum()),
                    **{f"sniped_{k}": float(v) for k, v in sniped.items()},
                    "sniped_total": float(sniped.sum()),
                    "late_losses": float(late),
                }
            )
            all_rows.append(frame)
    table = pd.DataFrame(per_event)
    pd.concat(all_rows).to_csv(os.path.join(CACHE, "polymarket_fed_snipe_trades.csv"), index=False)
    pd.set_option("display.width", 250)
    print("\n## Fed decisions on Polymarket: taker gains after the 14:00 ET statement ($)")
    cols = ["day", "winner", "p_winner_pre", "volume_1h_after", *[f"sniped_{k}" for k in BUCKET_LABELS], "sniped_total"]
    money = [c for c in cols if c not in ("day", "winner", "p_winner_pre")]
    print(table[cols].round(dict.fromkeys(money, 0) | {"p_winner_pre": 3}).to_string(index=False))
    print("\n## Totals over", len(table), "meetings")
    print(table[[f"sniped_{k}" for k in BUCKET_LABELS] + ["sniped_total", "late_losses"]].sum().round(0).to_string())
    surprise = table[table["p_winner_pre"] < 0.9]
    print(
        f"\nMeetings where the winner was priced < 0.90 just before 14:00: {len(surprise)}; "
        f"their sniped total ${surprise['sniped_total'].sum():,.0f} "
        f"of ${table['sniped_total'].sum():,.0f}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
