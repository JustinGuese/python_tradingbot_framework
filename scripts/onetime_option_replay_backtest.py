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
"""

import argparse
import os
import sys
from datetime import date

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
from tradingbot.utils.option_replay import PriceHistory, ReplayMarket, SurfaceReplayMarket, run_strategy
from tradingbot.utils.vol_indices import VIX, VIX3M, VVIX

MIN_DAYS = 40  # below this an alpha t-stat is meaningless

# name -> (decide(market, holdings), underlyings to load; None = every stored one)
STRATEGIES = {
    "indexvol": (
        lambda m, h: st.decide_indexvol(m, h, option_indexvolbot.OptionIndexVolBot.RULES, "SPY"),
        ["SPY"],
    ),
    "crossvol": (
        lambda m, h: st.decide_crossvol(m, h, option_crossvolbot.OptionCrossVolBot.RULES, option_crossvolbot.UNIVERSE),
        None,
    ),
    "earningscrush": (
        lambda m, h: st.decide_earningscrush(
            m, h, option_earningscrushbot.OptionEarningsCrushBot.RULES, option_earningscrushbot.UNIVERSE
        ),
        None,
    ),
    "mispricingscan": (
        lambda m, h: st.decide_mispricingscan(
            m,
            h,
            option_mispricingscanbot.OptionMispricingScanBot.RULES,
            option_mispricingscanbot.UNIVERSE,
            option_mispricingscanbot.ALWAYS,
        ),
        None,
    ),
    "dispersion": (
        lambda m, h: st.decide_dispersion(
            m,
            h,
            option_dispersionbot.OptionDispersionBot.RULES,
            option_dispersionbot.UNIVERSE,
            option_dispersionbot.INDEX,
        ),
        None,
    ),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", choices=sorted(STRATEGIES), required=True)
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
    args = ap.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    decide, underlyings = STRATEGIES[args.strategy]
    if args.surface or args.live_expiries:
        market = SurfaceReplayMarket(start, end, underlyings, live_expiries=args.live_expiries)
    else:
        market = ReplayMarket(start, end, underlyings)
    days = market.days
    print(f"option_quotes: {len(days)} days of chains ({days[0] if days else '-'} -> {days[-1] if days else '-'})")
    if len(days) < MIN_DAYS:
        print(f"{len(days)} days of history — too short for a backtest (need {MIN_DAYS}+).")
        return
    symbols = set(market.frame["underlying"]) | {"QQQ", VIX, VIX3M, VVIX}
    prices = PriceHistory.download(symbols, days[0])
    curve, book = run_strategy(market, decide, prices)
    if isinstance(market, SurfaceReplayMarket):
        total = max(market.recorded + market.modelled, 1)
        print(f"quotes: {market.recorded} recorded, {market.modelled} off the surface ({market.modelled / total:.0%})")
    split = curve.index[len(curve) // 2]
    qqq = prices.upto("QQQ", days[-1])["close"]
    print(HEADER)
    for label, part in (("full", curve), ("H1", curve.loc[:split]), ("H2", curve.loc[split:])):
        print(_row(args.strategy, label, metrics(part, qqq), book.trades if label == "full" else ""))


if __name__ == "__main__":
    main()
