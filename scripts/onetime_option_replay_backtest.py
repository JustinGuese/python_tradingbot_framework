"""Replay backtest of the option bots over stored option chains (option_quotes).

Unlike the synthetic backtests (Black-Scholes on a vol index), this fills at
the bid/ask the capture actually recorded and settles off the recorded
underlying price. It is the only way to judge the single-name strategies.

Each strategy is the live bot's own decide function (utils/option_strategies),
run through utils/option_replay.run_strategy. The replay cannot drift from
what the bot does; the three hand-written loops this script used to hold had
drifted.

  indexvol        option_IndexVolBot on SPY (a cross-check of the synthetic result)
  crossvol        option_CrossVolBot: condors on the names furthest above their Yang-Zhang forecast
  earningscrush   option_EarningsCrushBot: iron butterflies over reports priced rich
  mispricingscan  option_MispricingScanBot: both directions at |z| >= 2, vega-budgeted, hedged
  dispersion      option_DispersionBot: short SPY fly vs member straddles at rich implied correlation

The capture started 2026-09-28, so for months this will print "too short".
Imported historical chains work the moment they are in option_quotes with the
same columns (see utils/option_replay.py). Earnings timing, dividends and
macro dates come from the DB (corporateeventssnapshot, macrocalendarsnapshot),
and the scanner's shortlist and z-score history from option_mispricing_scan and
vol_surface (`python -m tradingbot.optionchainsnapshot --backfill` rebuilds
those for imported days). Reads the production DB: run with a port-forward
(kubectl port-forward -n tradingbots-2025 svc/psql-service 5432:5432) and
POSTGRES_URI pointing at it.

  uv run python scripts/onetime_option_replay_backtest.py --strategy crossvol [--start 2026-09-28]

Options for the DoltHub history:
  --live-expiries        enter the expiry the live bot would pick, quoted off the day's surface
  --names AAPL,MSFT      load only these underlyings
  --shortlist-all        crossvol / mispricingscan rank every loaded name (live shortlists by
                         yesterday's scan; with every chain loaded that is the same choice)
  --fill mid             fill every option leg at mid: the signal's P&L without the spread
  --trades-csv out.csv   one row per structure: entry, exit, P&L, spread paid, what the
                         decision saw (IV, fair vol, z), realized vol over the hold, and
                         whether a report fell inside it
Dispersion weights members by historical market caps (fundamentals.historical_market_caps):
stock_fundamentals only begins 2026-09-25.
"""

import argparse
import math
import os
import sys
from dataclasses import replace
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from onetime_option_bots_backtest import HEADER, _row, metrics

from tradingbot import (
    option_crossvolbot,
    option_dispersionbot,
    option_earningscrushbot,
    option_indexvolbot,
    option_mispricingscanbot,
)
from tradingbot.utils import option_strategies as st
from tradingbot.utils.fundamentals import historical_market_caps
from tradingbot.utils.option_replay import IRX, PriceHistory, ReplayMarket, SurfaceReplayMarket, run_strategy
from tradingbot.utils.vol_indices import VIX, VIX3M, VVIX

MIN_DAYS = 40  # below this an alpha t-stat is meaningless


def strategy(name: str, shortlist: int | None = None):
    """(decide(market, holdings), underlyings to load; None = every stored one). shortlist overrides the rules'."""

    def ranked(rules):
        return replace(rules, shortlist=shortlist) if shortlist else rules

    if name == "indexvol":
        rules = option_indexvolbot.OptionIndexVolBot.RULES
        return (lambda m, h: st.decide_indexvol(m, h, rules, "SPY")), ["SPY"]
    if name == "crossvol":
        rules = ranked(option_crossvolbot.OptionCrossVolBot.RULES)
        return (lambda m, h: st.decide_crossvol(m, h, rules, option_crossvolbot.UNIVERSE)), None
    if name == "earningscrush":
        rules = option_earningscrushbot.OptionEarningsCrushBot.RULES
        return (lambda m, h: st.decide_earningscrush(m, h, rules, option_earningscrushbot.UNIVERSE)), None
    if name == "mispricingscan":
        bot = option_mispricingscanbot
        rules = ranked(bot.OptionMispricingScanBot.RULES)
        return (lambda m, h: st.decide_mispricingscan(m, h, rules, bot.UNIVERSE, bot.ALWAYS)), None
    if name == "dispersion":
        bot = option_dispersionbot
        rules = bot.OptionDispersionBot.RULES
        return (lambda m, h: st.decide_dispersion(m, h, rules, bot.UNIVERSE, bot.INDEX)), None
    raise ValueError(name)


STRATEGIES = ("indexvol", "crossvol", "earningscrush", "mispricingscan", "dispersion")


def realized_vol(prices: PriceHistory, underlying: str, entry: date, exit_day: date) -> float | None:
    """Annualised close-to-close vol from the entry close to the exit close."""
    part = prices.upto(underlying, exit_day)
    if part is None:
        return None
    close = part["close"][part.index >= pd.Timestamp(entry)]
    rets = np.log(close).diff().dropna()
    return float(rets.std(ddof=0) * math.sqrt(252)) if len(rets) >= 2 else None


def trade_rows(book, market, prices: PriceHistory, last_day: date) -> pd.DataFrame:
    """The book's structures, closed and still held, with what each one's hold actually delivered."""
    rows = []
    for rec in [*book.records, *({**r, "exit": None, "exit_kind": "open"} for r in book.open_records())]:
        exit_day = rec["exit"] or last_day
        u = rec["underlying"]
        stored = market.events(u)
        reports = [d for d, _ in stored.earnings if rec["entry"] <= d <= exit_day] if stored else []
        rows.append(
            {
                **rec,
                "days": (exit_day - rec["entry"]).days,
                "realized_vol": realized_vol(prices, u, rec["entry"], exit_day),
                "report_in_hold": reports[0] if reports else None,
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", choices=STRATEGIES, required=True)
    ap.add_argument("--start", default="2000-01-01")
    ap.add_argument("--end", default=str(date.today()))
    ap.add_argument(
        "--surface",
        action="store_true",
        help="quote contracts a day did not record off its interpolated surface (sampled histories: DoltHub)",
    )
    ap.add_argument(
        "--live-expiries",
        action="store_true",
        help="with --surface: enter the expiry the live bot would pick, its chain quoted off the surface",
    )
    ap.add_argument("--names", help="comma-separated underlyings to load (default: the strategy's)")
    ap.add_argument("--shortlist-all", action="store_true", help="crossvol / mispricingscan: rank every loaded name")
    ap.add_argument("--fill", choices=("quote", "mid"), default="quote", help="option fills at bid/ask or at mid")
    ap.add_argument("--trades-csv", help="write one row per structure to this file")
    args = ap.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    _, underlyings = strategy(args.strategy)
    if args.names:
        underlyings = args.names.split(",")
    if args.surface or args.live_expiries:
        market = SurfaceReplayMarket(start, end, underlyings, live_expiries=args.live_expiries)
    else:
        market = ReplayMarket(start, end, underlyings)
    days = market.days
    print(f"option_quotes: {len(days)} days of chains ({days[0] if days else '-'} -> {days[-1] if days else '-'})")
    if len(days) < MIN_DAYS:
        print(f"{len(days)} days of history — too short for a backtest (need {MIN_DAYS}+).")
        return
    names = sorted(set(market.frame["underlying"]))
    decide, _ = strategy(args.strategy, len(names) if args.shortlist_all else None)
    prices = PriceHistory.download(set(names) | {"QQQ", VIX, VIX3M, VVIX, IRX}, days[0])
    if args.strategy == "dispersion":
        market.caps = historical_market_caps([u for u in names if u not in ("SPY", "QQQ")], days[0])
    print(f"{len(names)} names, fills at {'mid' if args.fill == 'mid' else 'bid/ask'}")
    curve, book = run_strategy(market, decide, prices, fill=args.fill)
    if isinstance(market, SurfaceReplayMarket):
        total = max(market.recorded + market.modelled, 1)
        print(f"quotes: {market.recorded} recorded, {market.modelled} off the surface ({market.modelled / total:.0%})")
    split = curve.index[len(curve) // 2]
    qqq = prices.upto("QQQ", days[-1])["close"]
    print(HEADER)
    for label, part in (("full", curve), ("H1", curve.loc[:split]), ("H2", curve.loc[split:])):
        print(_row(args.strategy, label, metrics(part, qqq), book.trades if label == "full" else ""))
    if args.trades_csv:
        trades = trade_rows(book, market, prices, days[-1])
        trades.to_csv(args.trades_csv, index=False)
        print(f"{len(trades)} structures -> {args.trades_csv}")


if __name__ == "__main__":
    main()
