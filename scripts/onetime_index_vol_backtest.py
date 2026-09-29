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
  --gates   round 3 (2026-09-28): two walk-forward grids ON TOP of the live rules.
            A: the vol-curve gates (^VIX/^VIX3M entry cap and unwind, ^VVIX cap,
               FOMC/CPI blackout), 2007 -> now (where ^VIX3M and ^VVIX exist).
            B: the fair-vol model (close-to-close HAR, Yang-Zhang HAR, GARCH),
               the signal (raw gap or its z-score against the past year) and a
               monthly tail hedge in 60-DTE 10-delta puts, 2000 -> now.
            Each: pick on H1, ship only if it beats the live rules on H2 without
            a deeper drawdown. CPI dates need FRED_API_KEY; without it the event
            gate is FOMC-only (said in the output).
  --pm      round 5 (2026-09-29): Kalshi release uncertainty (event_std_z, how
            unusually wide the CPI / unemployment ladder for the next print is;
            utils/prediction_market_features.py) as an entry gate and a size-down,
            on top of the live rules. 2021-10 -> now (the ladders' history), split
            2024-01-01 as in docs/backtests/prediction-markets-2026-09.md; pick on
            H1, same ship rule as --gates, then the decide path for the pick.
            Reads prediction_market_snapshots: needs POSTGRES_URI (port-forward).
  --decide  the live bot's own decision function (utils/option_strategies.
            decide_indexvol) on a synthetic chain built from the same model
            (SyntheticMarket). select_iron_condor picks the strikes, HAR runs
            on every close so far, horizons count NYSE sessions, and sizing
            uses cash after margin, all as live. It runs the live rules, gated and
            ungated, on the 2007+ window next to this fast simulator. The grids
            above stay on the fast simulator: the decide path is too slow for
            hundreds of 26-year runs, and it exists to show the two agree.

Results: docs/backtests/index-vol-2026-09.md, docs/backtests/option-round3-2026-09.md
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
from tradingbot.utils import macro_calendar as mc
from tradingbot.utils import option_math as om
from tradingbot.utils import option_rules as rl
from tradingbot.utils import options
from tradingbot.utils import vol_estimators as ve
from tradingbot.utils.option_decide import Close, Holdings, Market, Open
from tradingbot.utils.option_strategies import decide_indexvol

DATA_START, EVAL_START = "1999-03-10", "2000-03-10"  # QQQ (the benchmark) listed 1999-03-10


@dataclass(frozen=True)
class Underlying:
    symbol: str
    vol_index: str
    atm_ratio: float
    put_skew: float
    call_skew: float
    call_skew_elasticity: float = 0.0  # see Model.call_skew_at
    call_skew_cap: float = 1.0

    def model(self) -> Model:
        return Model(
            skew=self.put_skew,
            call_skew=self.call_skew,
            call_skew_elasticity=self.call_skew_elasticity,
            call_skew_cap=self.call_skew_cap,
        )


# SPY is calibrated on the real SPY chains of 2019-05 -> 2026-09 (the DoltHub
# backfill; docs/backtests/index-vol-real-surface-2026-09.md): ATM = 0.87 x VIX,
# put skew 0.28, and a call skew that rises with the vol level, 0.284 + 0.246
# ln(ATM / 0.20) capped at 0.40. The original one-day calibration (2026-09-25:
# 0.85 / 0.25 / a constant 0.08) priced upside wings too rich, placed 10-delta
# short calls 9% out of the money where the real market put them at 7%, and
# made the strategy look like t 3.2 over 2019-26 where real prices give 0.45.
# QQQ has no real chain history yet and keeps its one-day calibration.
UNDERLYINGS = {
    "SPY": Underlying("SPY", "^VIX", 0.87, 0.28, 0.284, 0.246, 0.40),
    "QQQ": Underlying("QQQ", "^VXN", 0.90, 0.21, 0.07),
}
ORIGINAL_CALIBRATION = {
    "SPY": Underlying("SPY", "^VIX", 0.85, 0.25, 0.08),  # --calibration original: the 2026-09 docs' numbers
    "QQQ": UNDERLYINGS["QQQ"],
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
        h = max(rl.weekdays(day, first_expiry(expiries, day, dte)), 1)
        out.append(om.har_rv_forecast(returns.loc[:ts], h))
    return out


def load_inputs(u: Underlying) -> tuple[pd.DataFrame, list[date]]:
    """Market frame with HAR fair vol per target DTE, cached for a day."""
    path = os.path.join(CACHE, f"index_vol_inputs_{u.symbol}_{date.today():%Y%m%d}.pkl")
    if os.path.exists(path):
        with open(path, "rb") as f:
            m, expiries = pickle.load(f)
        m["iv"] = m["vol_index"] / 100 * u.atm_ratio  # the cache is per symbol, the ratio per calibration
        return m, expiries
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


def _fair_other(model: str, returns: pd.Series, dvar: pd.Series, days: list, expiries: list[date], dte: int) -> list:
    out = []
    for ts in days:
        h = max(rl.weekdays(ts.date(), first_expiry(expiries, ts.date(), dte)), 1)
        if model == "har_yz":
            out.append(ve.har_forecast_from_daily_variance(dvar.loc[:ts], h))
        else:
            out.append(ve.garch11_forecast(returns.loc[:ts], h))
    return out


def _event_dates() -> tuple[list[date], str]:
    """FOMC statement days, plus CPI from FRED when FRED_API_KEY is set."""
    days, label = list(mc.FOMC_STATEMENT_DATES), "FOMC only (no FRED_API_KEY)"
    key = os.environ.get("FRED_API_KEY", "").strip()
    if key:
        days += mc.fetch_fred_release_dates(mc.FRED_RELEASES["CPI"], "2000-01-01", "2030-12-31", key)
        label = "FOMC + CPI"
    return sorted(set(days)), label


def load_round3(u: Underlying, dte: int = 35) -> tuple[pd.DataFrame, list[date], str]:
    """load_inputs plus the round-3 columns: vol curve, events, other fair models, z-scores."""
    m, expiries = load_inputs(u)
    events, event_label = _event_dates()
    path = os.path.join(CACHE, f"index_vol_round3_{u.symbol}_{dte}_{date.today():%Y%m%d}.pkl")
    if os.path.exists(path):
        with open(path, "rb") as f:
            extra = pickle.load(f)
    else:
        raw = _download(u.symbol)
        extra = pd.DataFrame(index=m.index)
        for sym, col in (("^VIX3M", "vix3m"), ("^VVIX", "vvix")):
            extra[col] = _download(sym)["Close"].reindex(m.index).ffill(limit=5)
        returns = om.log_returns(raw["Close"])
        ohlc = raw[["Open", "High", "Low", "Close"]].rename(columns=str.lower)
        dvar = ve.yang_zhang_daily_variance(ohlc)
        days = list(m.index)
        chunks = np.array_split(np.arange(len(days)), 32)
        for model in ("har_yz", "garch"):
            parts = Parallel(n_jobs=-1)(
                delayed(_fair_other)(model, returns, dvar, [days[i] for i in c], expiries, dte) for c in chunks
            )
            extra[f"fair_{model}_{dte}"] = [x for p in parts for x in p]
        with open(path, "wb") as f:
            pickle.dump(extra, f)
    m = m.join(extra)
    m["term"] = m["vix"] / m["vix3m"]
    m["bdays_to_event"] = mc.bdays_to_next_event_series(m.index, events)
    m[f"fair_har_{dte}"] = m[f"fair_{dte}"]
    for model in ("har", "har_yz", "garch"):
        spread = m["iv"] - m[f"fair_{model}_{dte}"]
        past = spread.shift(1).rolling(252, min_periods=60)
        m[f"z_{model}_{dte}"] = (spread - past.mean()) / past.std()
    return m, expiries, event_label


def _open_tail_hedge(h: Book, b: Book, day: date, row, expiries, r: rl.IndexVolRules) -> None:
    expiry = first_expiry(expiries, day, 60)
    k = round(h.model.strike_for_delta("P", 0.10, expiry, day, row))
    leg = Leg("P", k, expiry, 1)
    px = h.fill(h.mid(leg, day, row), row.S, True)
    n = int(r.tail_hedge_pct * (b.equity(day, row) + h.equity(day, row)) // (px * 100)) if px > 0 else 0
    if n > 0:
        h.open([Leg("P", k, expiry, 100 * n)], day, row)


def sim_index_condor(m: pd.DataFrame, expiries, r: rl.IndexVolRules, model: Model) -> tuple[pd.Series, Book]:
    fair_col = f"fair_{r.target_dte}" if r.fair_model == "har" else f"fair_{r.fair_model}_{r.target_dte}"
    fair = m[fair_col]
    z = m.get(f"z_{r.fair_model}_{r.target_dte}")
    b, curve = Book(model), {}
    hedge = Book(model, cash=0.0)  # the tail puts, kept apart so a condor exit leaves them alone
    month = None
    for ts, row in m.iterrows():
        day = ts.date()
        b.settle(day, row)
        hedge.settle(day, row)
        if r.tail_hedge_pct:
            if hedge.legs and hedge.dte(day) <= 21:
                hedge.close(day, row)
            if (day.year, day.month) != month and not hedge.legs:
                _open_tail_hedge(hedge, b, day, row, expiries, r)
            month = (day.year, day.month)
        term = getattr(row, "term", None)
        if b.legs:
            if rl.index_vol_exit_reason(-b.entry, b.value(day, row) - b.entry, b.dte(day), r) or (
                rl.index_vol_unwind_reason(term, r)
            ):
                b.close(day, row)
        elif rl.index_vol_entry_ok(
            row.iv,
            fair.loc[ts],
            row.vix,
            r,
            term_ratio=term,
            vvix=getattr(row, "vvix", None),
            bdays_to_event=getattr(row, "bdays_to_event", None),
            z=None if z is None else z.loc[ts],
            event_std_z=getattr(row, "event_std_z", None),
        ):
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
            budget = r.max_risk_pct * rl.index_vol_risk_mult(getattr(row, "event_std_z", None), r) * b.equity(day, row)
            n = int(min(budget, b.cash) // per_unit) if 0 < per_unit < math.inf else 0
            if n > 0:
                b.open([Leg(x.right, x.strike, x.expiry, x.qty * 100 * n) for x in unit], day, row)
        curve[ts] = b.equity(day, row) + hedge.equity(day, row)
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
        # 0.05 added 2026-09-29, before it was run: on real chains the call
        # wing is cheap (call skew ~0.3 at VIX 20-30), so a 10-delta short call
        # sits ~7% out of the money and the post-selloff rallies run through it.
        "call_delta": [0.05, 0.10, 0.16],
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


def _gate_grid(label: str, variants: list[rl.IndexVolRules], m, expiries, model, split=None) -> dict:
    split = m.index[len(m) // 2] if split is None else pd.Timestamp(split)
    live = evaluate(LIVE, m, expiries, model, split)
    results = Parallel(n_jobs=-1)(delayed(evaluate)(r, m, expiries, model, split) for r in variants)
    ranked = sorted(results, key=lambda x: x["H1"]["t"], reverse=True)
    print(
        f"\n### Grid {label}: {len(variants)} variants on top of the live rules, "
        f"{m.index[0].date()} -> {m.index[-1].date()}, split {split.date()}"
    )
    print(
        f"H2 t: live {live['H2']['t']:.2f}; mean of H1 top-10 {np.mean([x['H2']['t'] for x in ranked[:10]]):.2f}; "
        f"share of grid with H2 t > live {np.mean([x['H2']['t'] > live['H2']['t'] for x in results]):.0%}"
    )
    print(HEADER)
    for part in ("full", "H1", "H2"):
        print(_row("live rules", part, live[part], live["trades"] if part == "full" else ""))
    for i, x in enumerate(ranked[:8], 1):
        print(_row(f"#{i} {_diff(x['rules'], LIVE)}", "H1", x["H1"], x["trades"]))
        print(_row(f"#{i}", "H2", x["H2"]))
    best = ranked[0]
    ships = best["H2"]["t"] > live["H2"]["t"] and best["H2"]["max_dd"] >= live["H2"]["max_dd"]
    print(
        f"H1 winner: {_diff(best['rules'], LIVE)} -> H2 t {best['H2']['t']:.2f} (live {live['H2']['t']:.2f}), "
        f"H2 max DD {best['H2']['max_dd']:.1%} (live {live['H2']['max_dd']:.1%}): {'SHIPS' if ships else 'does not ship'}"
    )
    return {"best": best, "live": live, "ships": ships}


def gates(u: Underlying, model: Model) -> None:
    m, expiries, event_label = load_round3(u)
    print(f"Event gate uses: {event_label}")
    curve_window = m.loc[m[["vix3m", "vvix"]].dropna().index[0] :]
    grid_a = [
        replace(LIVE, max_term_ratio=t, max_vvix=v, event_blackout_bdays=e, unwind_term_ratio=w)
        for t, v, e, w in itertools.product(
            [None, 0.90, 0.95, 1.0], [None, 110.0, 130.0], [None, 1, 2], [None, 1.0, 1.05]
        )
    ]
    _gate_grid("A (vol curve, events)", grid_a, curve_window, expiries, model)
    signals = [
        {"min_gap": LIVE.min_gap, "min_z": None},
        {"min_gap": None, "min_z": 1.0},
        {"min_gap": None, "min_z": 1.5},
        {"min_gap": None, "min_z": 2.0},
        {"min_gap": LIVE.min_gap, "min_z": 1.0},
    ]
    grid_b = [
        replace(LIVE, fair_model=f, tail_hedge_pct=t, **sig)
        for f, sig, t in itertools.product(["har", "har_yz", "garch"], signals, [None, 0.0025, 0.005])
    ]
    _gate_grid("B (fair model, signal, tail hedge)", grid_b, m, expiries, model)


# ------------------------------------------------------------------
# --pm: prediction-market release uncertainty (round 5)
# ------------------------------------------------------------------

PM_SPLIT = "2024-01-01"


def load_pm(u: Underlying) -> tuple[pd.DataFrame, list[date]]:
    """load_round3 plus event_std_z (point-in-time, from the DB), cut to where it exists."""
    from tradingbot.utils import prediction_market_features as pmf

    # Only what the live rules read (the vol curve); not load_round3's GARCH / Yang-Zhang
    # fair vols over 1999+, which take an hour and are not used here.
    m, expiries = load_inputs(u)
    for sym, col in (("^VIX3M", "vix3m"), ("^VVIX", "vvix")):
        m[col] = _download(sym)["Close"].reindex(m.index).ffill(limit=5)
    m["term"] = m["vix"] / m["vix3m"]
    path = os.path.join(CACHE, f"index_vol_pm_{u.symbol}_{date.today():%Y%m%d}.pkl")
    if os.path.exists(path):
        z = pd.read_pickle(path)
    else:
        z = pmf.feature_frame(m.index, series=list(pmf.EVENT_SERIES))["event_std_z"]
        z.to_pickle(path)
    m["event_std_z"] = z.reindex(m.index)
    return m.loc[m["event_std_z"].first_valid_index() :], expiries


def pm(u: Underlying, model: Model) -> None:
    m, expiries = load_pm(u)
    z = m["event_std_z"]
    print(
        f"event_std_z: {z.notna().mean():.0%} of days known; share > 1.0 / 1.5 / 2.0: "
        f"{(z > 1).mean():.0%} / {(z > 1.5).mean():.0%} / {(z > 2).mean():.0%}"
    )
    variants = [
        replace(LIVE, max_event_std_z=g, event_size_z=s)
        for g, s in itertools.product([None, 1.0, 1.5, 2.0], [None, (1.0, 0.5), (1.5, 0.5)])
        if (g, s) != (None, None)
    ]
    res = _gate_grid("PM (release uncertainty)", variants, m, expiries, model, split=PM_SPLIT)
    pick = res["best"]["rules"]
    runs = Parallel(n_jobs=2)(
        delayed(_decide_eval)(label, r, m, expiries, model, pd.Timestamp(PM_SPLIT), m["S"])
        for label, r in (("decide: live rules", LIVE), (f"decide: {_diff(pick, LIVE)}", pick))
    )
    print("\n### Decide path, same window")
    print(HEADER)
    for label, parts, trades in runs:
        for part in ("full", "H1", "H2"):
            print(_row(label, part, parts[part], trades if part == "full" else ""))


# ------------------------------------------------------------------
# --decide: the live decision function on a synthetic chain
# ------------------------------------------------------------------

CHAIN_BAND = 0.35  # strikes listed within +/- 35% of spot, on a $1 grid (SPY's)
MIN_QUOTE = 0.01  # a contract is listed with a market down to a penny mid, as SPY's far wings are
_RATE = {"r": 0.0}  # the day's T-bill rate, for options.* IV solves inside decide


def _occ(u: str, expiry: date, right: str, strike: float) -> str:
    return f"{u}{expiry:%y%m%d}{right}{round(strike * 1000):08d}"


class SyntheticMarket(Market):
    """utils/option_decide.Market over one row of the synthetic frame."""

    def __init__(self, u: str, ts, row, spot_history: pd.Series, expiries, model: Model, book: Book):
        day = ts.date()
        super().__init__(day, pd.Timestamp(f"{day} 19:45", tz="UTC").to_pydatetime())
        self.u, self.ts, self.row, self.expiries, self.model, self.book = u, ts, row, expiries, model, book
        self.spot_history = spot_history  # the FULL series, not the evaluation window

    def chain(self, underlying: str, target_dte: int) -> options.ChainView:
        expiry = first_expiry(self.expiries, self.today, target_dte)
        S = self.row.S
        rows = []
        for k in range(max(int(S * (1 - CHAIN_BAND)), 1), int(S * (1 + CHAIN_BAND)) + 1):
            for right in ("C", "P"):
                mid = self.model.price(right, float(k), expiry, self.today, self.row)
                if mid < MIN_QUOTE:
                    continue
                rows.append(
                    {
                        "contract_symbol": _occ(underlying, expiry, right, k),
                        "option_type": right,
                        "strike": float(k),
                        "bid": self.book.fill(mid, S, False),
                        "ask": self.book.fill(mid, S, True),
                        "last_price": mid,
                        "volume": 1000.0,
                        "open_interest": 10_000.0,
                    }
                )
        return options.ChainView(underlying, expiry, pd.DataFrame(rows), S, True, self.today)

    def vol_index(self, symbol: str) -> float | None:
        col = {"^VIX": "vix", "^VIX3M": "vix3m", "^VVIX": "vvix"}[symbol]
        value = getattr(self.row, col, None)
        return None if value is None or pd.isna(value) else float(value)

    def closes(self, underlying: str) -> pd.Series:
        # Every close so far, as live now fetches (Market.closes).
        return self.spot_history.loc[: self.ts]

    def ohlc(self, symbols) -> dict:
        return {}

    def risk_free_rate(self) -> float:
        return float(self.row.r)

    def next_macro_event(self):
        bdays = getattr(self.row, "bdays_to_event", None)
        if bdays is None or pd.isna(bdays):
            return None, None
        return ("FOMC/CPI", "?"), int(bdays)

    def event_uncertainty(self) -> float | None:
        z = getattr(self.row, "event_std_z", None)
        return None if z is None or pd.isna(z) else float(z)


class SyntheticHoldings(Holdings):
    """utils/option_decide.Holdings over the harness's Book (legs keyed as OCC symbols)."""

    def __init__(self, u: str, day: date, row, book: Book, opened: dict):
        self.u, self.day, self.row, self.b, self._opened = u, day, row, book, opened

    def underlyings(self) -> set[str]:
        return {self.u} if self.b.legs else set()

    def book(self, underlying: str) -> options.OptionBook:
        keys = {_occ(underlying, x.expiry, x.right, x.strike): x for x in self.b.legs}
        return options.build_book(
            underlying,
            {k: x.qty for k, x in keys.items()},
            {k: self.b.mid(x, self.day, self.row) for k, x in keys.items()},
            self.row.S,
            {k: x.qty * x.paid for k, x in keys.items()},
            today=self.day,
            r=float(self.row.r),
            q=0.0,
        )

    def opened_on(self, underlying: str):
        return self._opened.get(underlying)

    def flows_since(self, underlying: str, since) -> float:
        return 0.0

    def equity(self) -> float:
        return self.b.equity(self.day, self.row)


def _execute_synthetic(actions, b: Book, day: date, row, opened: dict) -> None:
    for a in actions:
        if isinstance(a, Close):
            b.close(day, row)
            opened.pop(a.underlying, None)
        elif isinstance(a, Open):
            unit = [
                Leg(c.right, c.strike, c.expiry, lots)
                for c, lots in ((options.parse_occ(k), lots) for k, lots in a.pick.legs)
            ]
            priced = [om.Leg(x.right, x.strike, x.qty, b.fill(b.mid(x, day, row), row.S, x.qty > 0)) for x in unit]
            per_unit = om.max_loss(priced) * 100
            portfolio = {"USD": b.cash, **{_occ(a.pick.underlying, x.expiry, x.right, x.strike): x.qty for x in b.legs}}
            n = options.units_for_risk(per_unit, a.max_risk_usd, b.cash - options.margin_requirement(portfolio))
            if n > 0:
                b.open([Leg(x.right, x.strike, x.expiry, x.qty * 100 * n) for x in unit], day, row)
                opened[a.pick.underlying] = day


def sim_decide(m: pd.DataFrame, expiries, rules: rl.IndexVolRules, model: Model, spot_history: pd.Series, u="SPY"):
    """
    The live decide_indexvol, one step per day of `m`, on the synthetic chain.
    spot_history is the full close series: the HAR forecast looks back 5 years
    from each day, past the start of the evaluation window. Returns (curve, book).
    """
    import logging

    logging.getLogger("tradingbot").setLevel(logging.WARNING)  # decide logs every day
    real_rate, real_q = options.risk_free_rate, options.dividend_yield
    options.risk_free_rate = lambda: _RATE["r"]
    options.dividend_yield = lambda _u: 0.0
    try:
        b, curve, opened = Book(model), {}, {}
        for ts, row in m.iterrows():
            day = ts.date()
            _RATE["r"] = float(row.r)
            b.settle(day, row)
            if not b.legs:
                opened.pop(u, None)
            market = SyntheticMarket(u, ts, row, spot_history, expiries, model, b)
            actions = decide_indexvol(market, SyntheticHoldings(u, day, row, b, opened), rules, u)
            _execute_synthetic(actions, b, day, row, opened)
            curve[ts] = b.equity(day, row)
        return pd.Series(curve), b
    finally:
        options.risk_free_rate, options.dividend_yield = real_rate, real_q


def _decide_eval(label, rules, m, expiries, model, split, spot_history):
    curve, book = sim_decide(m, expiries, rules, model, spot_history)
    qqq = m["qqq"]
    parts = {"full": curve, "H1": curve.loc[:split], "H2": curve.loc[split:]}
    return label, {k: metrics(v, qqq) for k, v in parts.items()}, book.trades


def decide_check(u: Underlying, model: Model) -> None:
    """The live decision path vs the fast simulator, live rules gated and ungated, 2007+."""
    m, expiries, _ = load_round3(u)
    window = m.loc[m[["vix3m", "vvix"]].dropna().index[0] :]
    split = window.index[len(window) // 2]
    ungated = replace(LIVE, max_term_ratio=None)
    runs = Parallel(n_jobs=2)(
        delayed(_decide_eval)(label, r, window, expiries, model, split, m["S"])
        for label, r in (("decide: live rules (VIX/VIX3M <= 1.0)", LIVE), ("decide: ungated", ungated))
    )
    print(
        f"\n### Decide path vs fast simulator, {window.index[0].date()} -> {window.index[-1].date()}, split {split.date()}"
    )
    print(HEADER)
    for label, r in (("fast sim: live rules (VIX/VIX3M <= 1.0)", LIVE), ("fast sim: ungated", ungated)):
        x = evaluate(r, window, expiries, model, split)
        for part in ("full", "H1", "H2"):
            print(_row(label, part, x[part], x["trades"] if part == "full" else ""))
    for label, res, trades in runs:
        for part in ("full", "H1", "H2"):
            print(_row(label, part, res[part], trades if part == "full" else ""))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--underlying", default="SPY", choices=sorted(UNDERLYINGS))
    ap.add_argument("--tune", action="store_true")
    ap.add_argument("--gates", action="store_true", help="round-3 walk-forward of the new gates")
    ap.add_argument("--decide", action="store_true", help="the live decide path vs the fast simulator")
    ap.add_argument(
        "--calibration",
        choices=("real", "original"),
        default="real",
        help="real: fitted on the 2019-26 SPY chains (default); original: the one-day 2026-09-25 fit",
    )
    ap.add_argument("--pm", action="store_true", help="walk-forward of the Kalshi release-uncertainty gate")
    args = ap.parse_args()

    u = (UNDERLYINGS if args.calibration == "real" else ORIGINAL_CALIBRATION)[args.underlying]
    if args.pm:
        pm(u, u.model())
        return
    if args.decide:
        decide_check(u, u.model())
        return
    if args.gates:
        gates(u, u.model())
        return
    m, expiries = load_inputs(u)
    split = m.index[len(m) // 2]
    model = u.model()
    print(
        f"{u.symbol}: window {m.index[0].date()} -> {m.index[-1].date()}, split {split.date()}, "
        f"ATM = {u.atm_ratio} x {u.vol_index}, skew put {u.put_skew} / call {u.call_skew}"
        f" + {u.call_skew_elasticity} ln(ATM/0.20) (cap {u.call_skew_cap})"
    )
    signal_table(m)
    if args.tune:
        tune(m, expiries, model, split)
        return
    print("\n" + HEADER)
    x = evaluate(ORIGINAL, m, expiries, model, split)
    for label in ("full", "H1", "H2"):
        print(_row("option_IndexVolBot (defaults)", label, x[label], x["trades"] if label == "full" else ""))
    # The live rules gate on ^VIX / ^VIX3M, which starts in 2006: on the 2000+
    # frame (no vix3m column) the gate fails closed and nothing ever trades.
    r3, r3_expiries, _ = load_round3(u)
    r3 = r3.loc[r3[["vix3m"]].dropna().index[0] :]
    r3_split = r3.index[len(r3) // 2]
    x = evaluate(LIVE, r3, r3_expiries, model, r3_split)
    for label in ("full", "H1", "H2"):
        print(
            _row(
                f"option_IndexVolBot (live rules, {r3.index[0].year}+, split {r3_split.date()})",
                label,
                x[label],
                x["trades"] if label == "full" else "",
            )
        )
    bh = m["S"] / m["S"].iloc[0] * CAPITAL
    for label, part in (("full", bh), ("H1", bh.loc[:split]), ("H2", bh.loc[split:])):
        print(_row(f"{u.symbol} buy & hold", label, metrics(part, m["qqq"]), "-" if label == "full" else ""))


if __name__ == "__main__":
    main()
