"""Replay backtest of the option bots' rules over stored option chains (option_quotes).

Unlike the synthetic backtests (Black-Scholes on a vol index), this fills at
the bid/ask the capture actually recorded and settles off the recorded
underlying price. It is the only way to judge the single-name strategies:

  indexvol       option_IndexVolBot's rules on SPY (a cross-check of the synthetic result)
  crossvol       option_CrossVolBot: condors on the names furthest above their HAR forecast
  earningscrush  option_EarningsCrushBot: iron butterflies over reports priced rich

The capture started 2026-09-28, so for months this will print "too short".
Imported historical chains work the moment they are in option_quotes with the
same columns (see utils/option_replay.py). Reads the production DB: run with a
port-forward (kubectl port-forward -n tradingbots-2025 svc/psql-service 5432:5432)
and POSTGRES_URI pointing at it.

  uv run python scripts/onetime_option_replay_backtest.py --strategy crossvol [--start 2026-09-28]
"""

import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd
import yfinance as yf
from onetime_option_bots_backtest import HEADER, _row, metrics

from tradingbot.option_crossvolbot import OptionCrossVolBot
from tradingbot.option_earningscrushbot import OptionEarningsCrushBot
from tradingbot.option_indexvolbot import OptionIndexVolBot
from tradingbot.utils import option_math as om
from tradingbot.utils import option_rules as rl
from tradingbot.utils import options
from tradingbot.utils.option_replay import ReplayBook, ReplayMarket

MIN_DAYS = 40  # below this an alpha t-stat is meaningless


def _closes(symbols, start: date) -> dict[str, pd.Series]:
    data = yf.download(sorted(symbols), start=str(start - pd.Timedelta(days=900)), auto_adjust=True, progress=False)
    close = data["Close"]
    if isinstance(close, pd.Series):
        close = close.to_frame(sorted(symbols)[0])
    close.index = pd.DatetimeIndex(close.index).tz_localize(None).normalize()
    return {s: close[s].dropna() for s in close}


def _fair(close: pd.Series, day: date, expiry: date) -> float:
    past = close[close.index <= pd.Timestamp(day)]
    return om.har_rv_forecast(om.log_returns(past), max(rl.business_days(day, expiry), 1))


def run_indexvol(market: ReplayMarket, closes, vix: pd.Series) -> tuple[pd.Series, ReplayBook]:
    r, u = OptionIndexVolBot.RULES, "SPY"
    book, curve = ReplayBook(market), {}
    for day in market.days:
        book.settle(day)
        if u in book.underlyings():
            credit = max(-book.entry.get(u, 0.0), 0.0)
            if rl.index_vol_exit_reason(credit, book.pnl(u, day), book.dte(u, day), r):
                book.close(u, day)
        else:
            view = market.view(u, day, r.target_dte)
            v = vix[vix.index <= pd.Timestamp(day)]
            if view is not None and view.live and len(v):
                iv = options.atm_iv(view)
                if rl.index_vol_entry_ok(iv, _fair(closes[u], day, view.expiry), float(v.iloc[-1]), r):
                    pick = options.select_iron_condor(
                        u, r.put_delta, r.width_pct * view.spot, r.target_dte, view=view, call_delta=r.call_delta
                    )
                    per_unit = book.max_loss(pick, day)
                    if per_unit and per_unit > 0:
                        units = int(min(r.max_risk_pct * book.equity(day), book.cash) // per_unit)
                        if units > 0:
                            book.open(pick, units, day)
        curve[pd.Timestamp(day)] = book.equity(day)
    return pd.Series(curve), book


def run_crossvol(market: ReplayMarket, closes, vix: pd.Series) -> tuple[pd.Series, ReplayBook]:
    r = OptionCrossVolBot.RULES
    book, curve = ReplayBook(market), {}
    for day in market.days:
        book.settle(day)
        for u in sorted(book.underlyings()):
            credit = max(-book.entry.get(u, 0.0), 0.0)
            if rl.cross_vol_exit_reason(credit, book.pnl(u, day), book.dte(u, day), r):
                book.close(u, day)
        held = book.underlyings()
        v = vix[vix.index <= pd.Timestamp(day)]
        if len(held) < r.max_positions and len(v) and float(v.iloc[-1]) < r.max_vix:
            names, views = [], {}
            for u in market.underlyings(day):
                if u in ("SPY", "QQQ") or u in held or u not in closes:
                    continue
                view = market.view(u, day, r.target_dte)
                if view is None or not view.live:
                    continue
                # Earnings are not in option_quotes; a replay takes the report
                # dates from yfinance (fine for history, which is what it replays).
                clear = rl.earnings_clear(options.next_earnings_date(u, day), view.expiry, day)
                names.append(rl.NameVol(u, options.atm_iv(view), _fair(closes[u], day, view.expiry), clear))
                views[u] = view
            for n in rl.cross_vol_candidates(names, held, r):
                view = views[n.underlying]
                pick = options.select_iron_condor(
                    n.underlying, r.short_delta, r.width_pct * view.spot, r.target_dte, view=view
                )
                per_unit = book.max_loss(pick, day)
                if per_unit and per_unit > 0:
                    units = int(min(r.risk_per_name_pct * book.equity(day), book.cash) // per_unit)
                    if units > 0:
                        book.open(pick, units, day)
        curve[pd.Timestamp(day)] = book.equity(day)
    return pd.Series(curve), book


def run_earningscrush(market: ReplayMarket, closes, vix: pd.Series) -> tuple[pd.Series, ReplayBook]:
    r = OptionEarningsCrushBot.RULES
    book, curve = ReplayBook(market), {}
    events = {u: options.earnings_events(u) for u in set(market.frame["underlying"]) - {"SPY", "QQQ"}}
    opened: dict[str, date] = {}
    for day in market.days:
        book.settle(day)
        for u in sorted(book.underlyings()):
            if day > opened.get(u, day):
                book.close(u, day)
                opened.pop(u, None)
        for u, evs in events.items():
            if u in book.underlyings() or len(book.underlyings()) >= r.max_concurrent or u not in closes:
                continue
            upcoming = [e for e in evs if e[0] >= day]
            if not upcoming:
                continue
            reaction = rl.reaction_session(*upcoming[0])
            if reaction is None or rl.business_days(day, reaction) != 1:
                continue
            front = market.view(u, day, (reaction - day).days)
            if front is None or not front.live:
                continue
            back = market.view(u, day, (front.expiry - day).days + r.back_min_days_after_front)
            fi, bi = options.atm_iv(front), options.atm_iv(back) if back else None
            implied = om.implied_earnings_move(fi, front.T, bi, back.T) if fi and bi and back else None
            past = [e for e in evs if e[0] < day]
            reactions = om.earnings_reaction_returns(closes[u], [d for d, _ in past], dict(past)).tail(r.hist_events)
            hist = om.earnings_jump(reactions.tolist()) if len(reactions) else None
            ok, _ = rl.earnings_crush_entry_ok(day, reaction, implied, hist, len(reactions), r)
            if not ok:
                continue
            pick = options.select_iron_butterfly(front, r.wing_moves * implied * front.spot)
            per_unit = book.max_loss(pick, day)
            if per_unit and per_unit > 0:
                units = int(min(r.risk_per_trade_pct * book.equity(day), book.cash) // per_unit)
                if units > 0 and book.open(pick, units, day):
                    opened[u] = day
        curve[pd.Timestamp(day)] = book.equity(day)
    return pd.Series(curve), book


STRATEGIES = {"indexvol": run_indexvol, "crossvol": run_crossvol, "earningscrush": run_earningscrush}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", choices=sorted(STRATEGIES), required=True)
    ap.add_argument("--start", default="2000-01-01")
    ap.add_argument("--end", default=str(date.today()))
    args = ap.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    market = ReplayMarket(start, end, ["SPY"] if args.strategy == "indexvol" else None)
    days = market.days
    print(f"option_quotes: {len(days)} days of chains ({days[0] if days else '-'} -> {days[-1] if days else '-'})")
    if len(days) < MIN_DAYS:
        print(f"{len(days)} days of history — too short for a backtest (need {MIN_DAYS}+).")
        return
    closes = _closes(set(market.frame["underlying"]) | {"QQQ"}, days[0])
    vix = _closes({"^VIX"}, days[0])["^VIX"]
    curve, book = STRATEGIES[args.strategy](market, closes, vix)
    split = curve.index[len(curve) // 2]
    qqq = closes["QQQ"]
    print(HEADER)
    for label, part in (("full", curve), ("H1", curve.loc[:split]), ("H2", curve.loc[split:])):
        print(_row(args.strategy, label, metrics(part, qqq), book.trades if label == "full" else ""))


if __name__ == "__main__":
    main()
