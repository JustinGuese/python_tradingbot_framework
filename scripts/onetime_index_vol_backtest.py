"""Synthetic backtest + walk-forward re-tune of option_IndexVolBot: iron condors on SPY, 2000 -> now.

Why an index, after the AAPL option bots (docs/backtests/option-bots-*.md):
the mispricing test found that "IV above a HAR forecast" does predict the
variance premium, but a short at-the-money structure on one stock pays it back
in single-name jumps. An index jumps less, carries the larger premium, and
removes the hindsight of having picked AAPL.

It is also the most honest backtest the harness can run. ^VIX IS the 30-day
implied vol of the S&P 500 (and ^VXN of the Nasdaq-100), not a proxy scaled
from another asset, and it goes back through 2000-02 and 2008.

Pricing: Black-Scholes (utils/option_math) at
  ATM IV = ^VIX x ATM_RATIO, the smile iv(K) = atm x (1 - skew x z), z = ln(K/S)/(atm sqrt(T)),
with separate put and call skews. Calibrated on the live SPY chain of
2026-09-25: 28-66 DTE ATM IV was 0.82-0.92 x ^VIX, put skew 0.24-0.31, call
skew 0.08-0.12 (QQQ vs ^VXN: 0.86-0.97, 0.20-0.23, 0.07-0.10). ^VIX is a
variance-swap rate, which includes the skew, so treating it as ATM vol would
overstate every credit by 10-15%.

Expiries: every Friday (the live chain has weeklies, so "first expiry >= 45
days" lands 45-51 days out). Fills: the AAPL harness's, mid +/- max(0.0074% of
spot, 1.5% of premium) per leg. That is wider than SPY's quotes today and
narrower than before the 2008 penny pilot; OPTION_COST_SCALE=2 is the stress.

Decisions come from the live bot's rule functions (utils/option_rules
IndexVolRules), fair vol from the same HAR-RV forecast the live bot uses.

Modes:
  default   the live rules and the a-priori defaults, full window and both halves,
            plus the signal check (does IV - fair vol predict the premium?)
  --tune    walk-forward: grid-search on H1 (2000 .. mid-2013), judge on H2.
  --underlying QQQ   the same on QQQ / ^VXN (a robustness check, not a pick)

Results: docs/backtests/index-vol-2026-09.md
"""

import argparse
import bisect
import itertools
import math
import os
import pickle
import sys
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pandas as pd
import yfinance as yf
from joblib import Parallel, delayed
from onetime_option_bots_backtest import (
    CACHE,
    CAPITAL,
    HEADER,
    Book,
    Leg,
    Model,
    _diff,
    _row,
    metrics,
)

from tradingbot.option_indexvolbot import OptionIndexVolBot
from tradingbot.utils import option_math as om
from tradingbot.utils import option_rules as rl

DATA_START, EVAL_START = "1999-03-10", "2000-03-10"  # QQQ (the benchmark) listed 1999-03-10


@dataclass(frozen=True)
class Underlying:
    symbol: str
    vol_index: str
    atm_ratio: float
    put_skew: float
    call_skew: float


UNDERLYINGS = {
    "SPY": Underlying("SPY", "^VIX", 0.85, 0.25, 0.08),
    "QQQ": Underlying("QQQ", "^VXN", 0.90, 0.21, 0.07),
}
DTES = (35, 45, 60)
ORIGINAL = rl.IndexVolRules()
LIVE = OptionIndexVolBot.RULES


def _download(symbol: str) -> pd.DataFrame:
    raw = yf.download(symbol, start=DATA_START, auto_adjust=True, progress=False)
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw.index = pd.to_datetime(raw.index).tz_localize(None).normalize()
    return raw


def fridays(start: date, end: date) -> list[date]:
    first = start + timedelta(days=(4 - start.weekday()) % 7)
    return [first + timedelta(weeks=i) for i in range((end - first).days // 7 + 20)]


def first_expiry(expiries: list[date], today: date, min_days: int) -> date:
    return expiries[bisect.bisect_left(expiries, today + timedelta(days=min_days))]


def _fair(returns: pd.Series, days: list, expiries: list[date], dte: int) -> list[float]:
    out = []
    for ts in days:
        day = ts.date()
        h = max(rl.business_days(day, first_expiry(expiries, day, dte)), 1)
        out.append(om.har_rv_forecast(returns.loc[:ts], h))
    return out


def load_inputs(u: Underlying) -> tuple[pd.DataFrame, list[date]]:
    """Market frame with HAR fair vol per target DTE, cached for a day."""
    path = os.path.join(CACHE, f"index_vol_inputs_{u.symbol}_{date.today():%Y%m%d}.pkl")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return pickle.load(f)
    raw = _download(u.symbol)
    m = pd.DataFrame(index=raw.index)
    m["S"] = raw["Close"]
    for sym, col in (("QQQ", "qqq"), (u.vol_index, "vol_index"), ("^VIX", "vix"), ("^IRX", "irx")):
        m[col] = _download(sym)["Close"].reindex(m.index).ffill()
    m["r"] = (m["irx"] / 100).clip(lower=0).fillna(0.0)
    m["iv"] = m["vol_index"] / 100 * u.atm_ratio
    returns = om.log_returns(m["S"])
    # Realized vol over the next 21 sessions: what the option actually had to pay for.
    m["rv_fwd21"] = returns[::-1].rolling(21).std()[::-1].shift(-1) * math.sqrt(252)
    m = m.loc[EVAL_START:].dropna(subset=["iv", "qqq"])
    expiries = fridays(m.index[0].date(), m.index[-1].date())
    days = list(m.index)
    chunks = np.array_split(np.arange(len(days)), 16)
    for dte in DTES:
        parts = Parallel(n_jobs=-1)(delayed(_fair)(returns, [days[i] for i in c], expiries, dte) for c in chunks)
        m[f"fair_{dte}"] = [x for p in parts for x in p]
    out = (m, expiries)
    with open(path, "wb") as f:
        pickle.dump(out, f)
    return out


def sim_index_condor(m: pd.DataFrame, expiries, r: rl.IndexVolRules, model: Model) -> tuple[pd.Series, Book]:
    fair = m[f"fair_{r.target_dte}"]
    b, curve = Book(model), {}
    for ts, row in m.iterrows():
        day = ts.date()
        b.settle(day, row)
        if b.legs:
            if rl.index_vol_exit_reason(-b.entry, b.value(day, row) - b.entry, b.dte(day), r):
                b.close(day, row)
        elif rl.index_vol_entry_ok(row.iv, fair.loc[ts], row.vix, r):
            expiry = first_expiry(expiries, day, r.target_dte)
            w = max(round(r.width_pct * row.S), 1.0)
            kp = max(round(model.strike_for_delta("P", r.put_delta, expiry, day, row)), w + 1)
            kc = round(model.strike_for_delta("C", r.call_delta, expiry, day, row))
            unit = [
                Leg("P", kp - w, expiry, 1),
                Leg("P", kp, expiry, -1),
                Leg("C", kc, expiry, -1),
                Leg("C", kc + w, expiry, 1),
            ]
            priced = [om.Leg(x.right, x.strike, x.qty, b.fill(b.mid(x, day, row), row.S, x.qty > 0)) for x in unit]
            per_unit = om.max_loss(priced) * 100
            n = int(min(r.max_risk_pct * b.equity(day, row), b.cash) // per_unit) if 0 < per_unit < math.inf else 0
            if n > 0:
                b.open([Leg(x.right, x.strike, x.expiry, x.qty * 100 * n) for x in unit], day, row)
        curve[ts] = b.equity(day, row)
    return pd.Series(curve), b


def evaluate(rules, m, expiries, model, split) -> dict:
    curve, book = sim_index_condor(m, expiries, rules, model)
    qqq = m["qqq"]
    return {
        "rules": rules,
        "trades": book.trades,
        "full": metrics(curve, qqq),
        "H1": metrics(curve.loc[:split], qqq),
        "H2": metrics(curve.loc[split:], qqq),
    }


def signal_table(m: pd.DataFrame, dte: int = 35) -> None:
    """Does ATM IV - HAR fair vol predict the premium (ATM IV - the next 21 days' realized vol)?"""
    d = m.dropna(subset=["rv_fwd21"])
    gap = d["iv"] - d[f"fair_{dte}"]
    prem = d["iv"] - d["rv_fwd21"]
    bins = [-1, -0.03, 0, 0.03, 0.06, 0.10, 1]
    labels = ["< -3", "-3 .. 0", "0 .. 3", "3 .. 6", "6 .. 10", "> 10"]
    print(f"\nSignal: ATM IV - HAR fair ({dte}d) vs ATM IV - realized next 21d; corr {gap.corr(prem):.2f}")
    print(
        "| IV - fair (pts) | Days | Mean IV | Mean fair | Realized next 21d | IV - realized, mean | Share IV > realized |"
    )
    print("|---|---|---|---|---|---|---|")
    buckets = pd.cut(gap, bins, labels=labels)
    for label in labels:
        s, p = d[buckets == label], prem[buckets == label]
        if not len(s):
            continue
        print(
            f"| {label} | {len(s)} | {s['iv'].mean():.1%} | {s[f'fair_{dte}'].mean():.1%} | "
            f"{s['rv_fwd21'].mean():.1%} | {p.mean() * 100:+.1f} | {(p > 0).mean():.0%} |"
        )
    print(f"Median gap {gap.median():+.3f}; HAR MAE {(d[f'fair_{dte}'] - d['rv_fwd21']).abs().mean():.3f}")


def grid() -> list[rl.IndexVolRules]:
    axes = {
        "target_dte": [35, 60],
        "put_delta": [0.10, 0.16, 0.20],
        "call_delta": [0.10, 0.16],
        "width_pct": [0.03, 0.05, 0.10],
        "take_profit": [0.5, 0.75],
        "stop_loss": [2.0, 99.0],
        "exit_dte": [7, 21],
        "min_gap": [None, 0.0, 0.03],
    }
    keys = list(axes)
    return [replace(ORIGINAL, **dict(zip(keys, c, strict=True))) for c in itertools.product(*axes.values())]


def tune(m, expiries, model, split) -> None:
    combos = grid()
    results = Parallel(n_jobs=-1)(delayed(evaluate)(r, m, expiries, model, split) for r in combos)
    base = evaluate(ORIGINAL, m, expiries, model, split)
    ranked = sorted(results, key=lambda x: x["H1"]["t"], reverse=True)
    print(f"\n### option_IndexVolBot: {len(combos)} combos, picked on H1 alpha t, judged on H2")
    print(
        f"H2 t: defaults {base['H2']['t']:.2f}; mean of H1 top-10 {np.mean([x['H2']['t'] for x in ranked[:10]]):.2f}; "
        f"share of grid with H2 t>0 {np.mean([x['H2']['t'] > 0 for x in results]):.0%}"
    )
    print(HEADER)
    print(_row("defaults", "H1", base["H1"], base["trades"]))
    print(_row("defaults", "H2", base["H2"]))
    for i, x in enumerate(ranked[:8], 1):
        print(_row(f"#{i} {_diff(x['rules'], ORIGINAL)}", "H1", x["H1"], x["trades"]))
        print(_row(f"#{i}", "H2", x["H2"]))
    # The single H1 winner is one draw from a noisy ranking. The consensus of
    # the H1 top-10 (each parameter's most common value) is the plateau.
    top = [vars(x["rules"]) for x in ranked[:10]]
    modal = {k: Counter(t[k] for t in top).most_common(1)[0][0] for k in top[0]}
    consensus = evaluate(rl.IndexVolRules(**modal), m, expiries, model, split)
    print(
        _row(f"H1 top-10 consensus: {_diff(consensus['rules'], ORIGINAL)}", "H1", consensus["H1"], consensus["trades"])
    )
    print(_row("H1 top-10 consensus", "H2", consensus["H2"]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--underlying", default="SPY", choices=sorted(UNDERLYINGS))
    ap.add_argument("--tune", action="store_true")
    args = ap.parse_args()

    u = UNDERLYINGS[args.underlying]
    m, expiries = load_inputs(u)
    split = m.index[len(m) // 2]
    model = Model(skew=u.put_skew, call_skew=u.call_skew)
    print(
        f"{u.symbol}: window {m.index[0].date()} -> {m.index[-1].date()}, split {split.date()}, "
        f"ATM = {u.atm_ratio} x {u.vol_index}, skew put {u.put_skew} / call {u.call_skew}"
    )
    signal_table(m)
    if args.tune:
        tune(m, expiries, model, split)
        return
    print("\n" + HEADER)
    rows = [("defaults", ORIGINAL)] if LIVE == ORIGINAL else [("live rules", LIVE), ("defaults", ORIGINAL)]
    for label_rules, rules in rows:
        x = evaluate(rules, m, expiries, model, split)
        for label in ("full", "H1", "H2"):
            print(_row(f"option_IndexVolBot ({label_rules})", label, x[label], x["trades"] if label == "full" else ""))
    bh = m["S"] / m["S"].iloc[0] * CAPITAL
    for label, part in (("full", bh), ("H1", bh.loc[:split]), ("H2", bh.loc[split:])):
        print(_row(f"{u.symbol} buy & hold", label, metrics(part, m["qqq"]), "-" if label == "full" else ""))


if __name__ == "__main__":
    main()
