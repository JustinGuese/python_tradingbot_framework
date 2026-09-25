"""Synthetic backtest + walk-forward re-tune of the option_* bots on AAPL, 2012 -> now.

yfinance has no historical option chains, so every option here is PRICED, not
observed: Black-Scholes (utils/option_math) on an implied-vol proxy. The bots'
own rule functions (utils/option_rules) make every decision, so the rules
are exactly the live ones; only the prices are synthetic.

IV proxy: ^VXN (Nasdaq-100 implied vol, a real traded-options series) scaled
by AAPL's realized vol relative to QQQ's, as a trailing 1-year median ratio.
That carries the INDEX variance risk premium over to AAPL.

Skew (--skew, default 0.15): IV rises below the money as
iv(K) = atm x (1 - skew x z), z = ln(K/S) / (atm sqrt(T)), for K < S; flat above.
0.15 is the conservative end of what the live AAPL chain showed on 2026-09-25
(-0.15 .. -0.21 on the 30-85 day puts; calls ~flat). --skew 0 is flat vol.

Not modelled: earnings IV run-up and crush (the catalyst bot's thesis), term
structure (a LEAP gets 30-day vol and its swings), American exercise, and the
dividends in the auto-adjusted price path.

Strikes: a grid of 1.25% of spot. Spread width: the live $10 at $336 is 3% of
spot, kept relative so pre-split history is comparable. Fills: mid +/- max($0.025
at $336, 1.5% of premium) per leg.

Modes:
  default   the live rules, full window and both halves
  --tune    walk-forward: grid-search on H1 (2012-06 .. 2019-07), judge on H2.
            A change ships only if it beats the live defaults out of sample.

Results: docs/backtests/option-bots-2026-09.md
"""

import argparse
import bisect
import itertools
import logging
import math
import os
import pickle
import warnings
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)
os.environ.setdefault("POSTGRES_URI", "stub:stub@localhost:5432/stub")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yfinance as yf  # noqa: E402
from joblib import Parallel, delayed  # noqa: E402
from ta.trend import ADXIndicator  # noqa: E402

from tradingbot.option_catalystcallbot import OptionCatalystCallBot  # noqa: E402
from tradingbot.option_creditspreadbot import OptionCreditSpreadBot  # noqa: E402
from tradingbot.option_ironcondorbot import OptionIronCondorBot  # noqa: E402
from tradingbot.option_leapcallbot import OptionLeapCallBot  # noqa: E402
from tradingbot.utils import option_math as om  # noqa: E402
from tradingbot.utils import option_rules as rl  # noqa: E402
from tradingbot.utils.backtest import _compute_alpha_metrics  # noqa: E402

DATA_START, EVAL_START = "2010-06-01", "2012-06-01"
CAPITAL = 100_000.0
REF_PRICE = 336.0  # AAPL when the live rules were written: $10 width = 3%
UNDERLYING = "AAPL"
CACHE = os.environ.get("RESEARCH_CACHE", "/tmp")
COST_SCALE = float(os.environ.get("OPTION_COST_SCALE", "1.0"))  # sensitivity: 0 = fills at mid

# The rules the live bots run, read from the bots themselves so they cannot drift.
LIVE = {
    "option_LeapCallBot": OptionLeapCallBot.RULES,
    "option_CreditSpreadBot": OptionCreditSpreadBot.RULES,
    "option_IronCondorBot": OptionIronCondorBot.RULES,
    "option_CatalystCallBot": OptionCatalystCallBot.RULES,
}
# The rules as first shipped, before the 2026-09-25 walk-forward re-tune.
ORIGINAL = {
    "option_LeapCallBot": rl.LeapRules(),
    "option_CreditSpreadBot": rl.CreditRules(),
    "option_IronCondorBot": rl.CreditRules(short_delta=0.16, min_iv_hv=1.15, max_adx=25.0),
    "option_CatalystCallBot": rl.CatalystRules(),
}


# ------------------------------------------------------------------
# Data
# ------------------------------------------------------------------


def _download(symbol: str) -> pd.DataFrame:
    raw = yf.download(symbol, start=DATA_START, auto_adjust=True, progress=False)
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw.index = pd.to_datetime(raw.index).tz_localize(None).normalize()
    return raw


def _build_market() -> pd.DataFrame:
    aapl = _download(UNDERLYING)
    m = pd.DataFrame(index=aapl.index)
    m["S"], m["high"], m["low"] = aapl["Close"], aapl["High"], aapl["Low"]
    for sym, col in (("QQQ", "qqq"), ("^VXN", "vxn"), ("^VIX", "vix"), ("^IRX", "irx")):
        m[col] = _download(sym)["Close"].reindex(m.index).ffill()
    m["r"] = (m["irx"] / 100).clip(lower=0).fillna(0.0)
    hv30_a = om.rolling_historical_volatility(m["S"], 30)
    hv30_q = om.rolling_historical_volatility(m["qqq"], 30)
    m["iv"] = m["vxn"] / 100 * (hv30_a / hv30_q).rolling(252).median()
    m["hv20"] = om.rolling_historical_volatility(m["S"], 20)
    m["hv60"] = om.rolling_historical_volatility(m["S"], 60)
    m["sma50"] = m["S"].rolling(50).mean()
    m["sma200"] = m["S"].rolling(200).mean()
    m["adx"] = ADXIndicator(m["high"], m["low"], m["S"], window=14).adx()
    return m.loc[EVAL_START:].dropna(subset=["iv", "sma200", "hv60", "adx"])


def load_inputs() -> tuple[pd.DataFrame, list[date]]:
    """Market frame and earnings dates, cached for a day (the tune runs hundreds of sims)."""
    path = os.path.join(CACHE, f"option_bots_inputs_{date.today():%Y%m%d}.pkl")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return pickle.load(f)
    df = yf.Ticker(UNDERLYING).get_earnings_dates(limit=100)
    earnings = sorted({ts.date() for ts in pd.to_datetime(df.index)})
    out = (_build_market(), earnings)
    with open(path, "wb") as f:
        pickle.dump(out, f)
    return out


def monthly_expiries(start: date, end: date) -> list[date]:
    """Third Fridays: the standard monthly expiry."""
    out = []
    for y in range(start.year, end.year + 3):
        for mo in range(1, 13):
            first = date(y, mo, 1)
            out.append(first + timedelta(days=(4 - first.weekday()) % 7 + 14))
    return out


def first_expiry(expiries: list[date], today: date, min_days: int) -> date:
    return expiries[bisect.bisect_left(expiries, today + timedelta(days=min_days))]


def next_earnings(earnings: list[date], today: date) -> date | None:
    i = bisect.bisect_left(earnings, today)
    return earnings[i] if i < len(earnings) else None


def snap(k: float, spot: float) -> float:
    step = 0.0125 * spot
    return round(k / step) * step


# ------------------------------------------------------------------
# Pricing with skew, and a book of synthetic legs
# ------------------------------------------------------------------


@dataclass(frozen=True)
class Model:
    skew: float = 0.15

    def vol(self, atm: float, S: float, K: float, T: float) -> float:
        if K >= S or T <= 0 or self.skew <= 0:
            return atm
        z = math.log(K / S) / (atm * math.sqrt(T))
        return atm * min(1.0 - self.skew * z, 2.5)

    def price(self, right: str, K: float, expiry: date, day: date, row) -> float:
        T = om.year_fraction(expiry, day)
        return om.bs_price(row.S, K, T, row.r, self.vol(row.iv, row.S, K, T), right)

    def delta(self, right: str, K: float, expiry: date, day: date, row) -> float:
        T = om.year_fraction(expiry, day)
        return om.delta(row.S, K, T, row.r, self.vol(row.iv, row.S, K, T), right)

    def strike_for_delta(self, right: str, target: float, expiry: date, day: date, row) -> float:
        """Delta-targeted strike under the skewed smile (two fixed-point passes), snapped."""
        T = om.year_fraction(expiry, day)
        k = om.strike_for_delta(row.S, T, row.r, row.iv, target, right)
        for _ in range(2):
            k = om.strike_for_delta(row.S, T, row.r, self.vol(row.iv, row.S, k, T), target, right)
        return snap(k, row.S)


@dataclass
class Leg:
    right: str
    strike: float
    expiry: date
    qty: float  # signed share-equivalents


@dataclass
class Book:
    model: Model
    cash: float = CAPITAL
    legs: list[Leg] = field(default_factory=list)
    entry: float = 0.0  # signed: + paid, - received
    trades: int = 0

    def mid(self, leg: Leg, day: date, row) -> float:
        return self.model.price(leg.right, leg.strike, leg.expiry, day, row)

    @staticmethod
    def fill(mid: float, spot: float, buy: bool) -> float:
        half = COST_SCALE * max(0.025 * spot / REF_PRICE, 0.015 * mid)
        return mid + half if buy else max(mid - half, 0.0)

    def value(self, day: date, row) -> float:
        return sum(leg.qty * self.mid(leg, day, row) for leg in self.legs)

    def equity(self, day: date, row) -> float:
        return self.cash + self.value(day, row)

    def delta_dollars(self, day: date, row) -> float:
        return sum(leg.qty * self.model.delta(leg.right, leg.strike, leg.expiry, day, row) for leg in self.legs) * row.S

    def dte(self, day: date) -> int:
        return min((leg.expiry - day).days for leg in self.legs)

    def open(self, legs: list[Leg], day: date, row) -> None:
        for leg in legs:
            px = self.fill(self.mid(leg, day, row), row.S, leg.qty > 0)
            self.cash -= leg.qty * px
            self.entry += leg.qty * px
        self.legs.extend(legs)
        self.trades += 1

    def trade(self, leg: Leg, qty: float, day: date, row) -> float:
        """Partially close `leg` by `qty` (signed toward flat). Returns cash."""
        px = self.fill(self.mid(leg, day, row), row.S, qty > 0)
        cash = -qty * px
        self.cash += cash
        self.entry *= (leg.qty + qty) / leg.qty
        leg.qty += qty
        return cash

    def close(self, day: date, row) -> float:
        proceeds = sum(leg.qty * self.fill(self.mid(leg, day, row), row.S, leg.qty < 0) for leg in self.legs)
        self.cash += proceeds
        self.legs, self.entry = [], 0.0
        return proceeds

    def settle(self, day: date, row) -> None:
        for leg in [leg for leg in self.legs if leg.expiry <= day]:
            self.cash += leg.qty * om.intrinsic(row.S, leg.strike, leg.right)
        self.legs = [leg for leg in self.legs if leg.expiry > day]
        if not self.legs:
            self.entry = 0.0


# ------------------------------------------------------------------
# The bots, driven by their live rule functions
# ------------------------------------------------------------------


def sim_leap(m, expiries, _earn, r: rl.LeapRules, model: Model) -> tuple[pd.Series, Book]:
    b, curve = Book(model), {}
    for ts, row in m.iterrows():
        day = ts.date()
        b.settle(day, row)
        signal = rl.leap_signal(row.S, row.sma200, r)
        if b.legs:
            if signal == -1:
                b.close(day, row)
            elif b.dte(day) <= r.roll_dte:  # the framework's auto-roll: re-buy with the proceeds
                _buy_leap(b, day, row, expiries, r, budget=b.close(day, row))
            else:
                leg = b.legs[0]
                per = model.delta("C", leg.strike, leg.expiry, day, row) * row.S * 100
                n = rl.leap_trim_contracts(b.delta_dollars(day, row), b.equity(day, row), per, r)
                if n > 0:
                    b.trade(leg, -min(n * 100, leg.qty), day, row)
                    if leg.qty <= 0:
                        b.legs, b.entry = [], 0.0
        elif signal == 1 and om.iv_hv_ratio(row.iv, row.hv60) <= r.max_iv_hv:
            n = rl.leap_contracts(b.equity(day, row), row.S, r.delta, r.leverage)
            _buy_leap(b, day, row, expiries, r, contracts=n)
        curve[ts] = b.equity(day, row)
    return pd.Series(curve), b


def _buy_leap(b: Book, day, row, expiries, r: rl.LeapRules, budget: float | None = None, contracts: int = 0):
    expiry = first_expiry(expiries, day, r.target_dte)
    k = b.model.strike_for_delta("C", r.delta, expiry, day, row)
    ask = b.fill(b.model.price("C", k, expiry, day, row), row.S, True)
    affordable = int(min(b.cash, budget if budget is not None else b.cash) // (ask * 100))
    n = affordable if budget is not None else min(contracts, affordable)
    if n > 0:
        b.open([Leg("C", k, expiry, 100 * n)], day, row)


def sim_credit(m, expiries, earnings, rules: rl.CreditRules, model: Model, condor: bool) -> tuple[pd.Series, Book]:
    b, curve = Book(model), {}
    for ts, row in m.iterrows():
        day = ts.date()
        b.settle(day, row)
        if b.legs:
            pnl = b.value(day, row) - b.entry
            if rl.credit_exit_reason(-b.entry, pnl, b.dte(day), rules):
                b.close(day, row)
        else:
            side = "both" if condor else rl.credit_side(row.S, row.sma50, row.sma200, rules)
            expiry = first_expiry(expiries, day, rules.target_dte)
            if (
                side is not None
                and rl.premium_selling_ok(om.iv_hv_ratio(row.iv, row.hv20), row.vix, row.adx, rules)
                and rl.earnings_clear(next_earnings(earnings, day), expiry, day)
            ):
                _open_credit(b, day, row, expiry, rules, side)
        curve[ts] = b.equity(day, row)
    return pd.Series(curve), b


def _open_credit(b: Book, day, row, expiry, rules: rl.CreditRules, side: str) -> None:
    rights = ["P", "C"] if side == "both" else ["P" if side == "bull" else "C"]
    width = max(snap(rules.width / REF_PRICE * row.S, row.S), 0.0125 * row.S)
    unit: list[Leg] = []
    for right in rights:
        d = rules.call_delta if right == "C" and rules.call_delta is not None else rules.short_delta
        ks = b.model.strike_for_delta(right, d, expiry, day, row)
        kl = ks - width if right == "P" else ks + width
        unit += [Leg(right, ks, expiry, -1), Leg(right, kl, expiry, 1)]
    priced = [om.Leg(x.right, x.strike, x.qty, b.fill(b.mid(x, day, row), row.S, x.qty > 0)) for x in unit]
    per_unit = om.max_loss(priced) * 100
    if not 0 < per_unit < math.inf:
        return
    n = int(min(rules.max_risk_pct * b.equity(day, row), b.cash) // per_unit)
    if n > 0:
        b.open([Leg(x.right, x.strike, x.expiry, x.qty * 100 * n) for x in unit], day, row)


def sim_catalyst(m, expiries, earnings, r: rl.CatalystRules, model: Model) -> tuple[pd.Series, Book]:
    b, curve = Book(model), {}
    for ts, row in m.iterrows():
        day = ts.date()
        b.settle(day, row)
        ne = next_earnings(earnings, day)
        if b.legs:
            pnl_pct = (b.value(day, row) - b.entry) / abs(b.entry) if b.entry else 0.0
            if rl.catalyst_exit_reason(day, ne, b.legs[0].expiry, pnl_pct, r):
                b.close(day, row)
        elif rl.catalyst_entry_ok(day, ne, r) and row.sma50 < row.S and om.iv_hv_ratio(row.iv, row.hv20) <= r.max_iv_hv:
            expiry = first_expiry(expiries, day, (ne - day).days + r.days_after_earnings)
            k = model.strike_for_delta("C", r.delta, expiry, day, row)
            ask = b.fill(model.price("C", k, expiry, day, row), row.S, True)
            n = int(r.position_pct * b.equity(day, row) // (ask * 100))
            if n > 0:
                b.open([Leg("C", k, expiry, 100 * n)], day, row)
        curve[ts] = b.equity(day, row)
    return pd.Series(curve), b


def simulate(name: str, rules, m, expiries, earnings, model: Model) -> tuple[pd.Series, Book]:
    if name == "option_LeapCallBot":
        return sim_leap(m, expiries, earnings, rules, model)
    if name == "option_CatalystCallBot":
        return sim_catalyst(m, expiries, earnings, rules, model)
    return sim_credit(m, expiries, earnings, rules, model, condor=name == "option_IronCondorBot")


# ------------------------------------------------------------------
# Metrics: alpha vs QQQ (the project target), both halves
# ------------------------------------------------------------------


def metrics(curve: pd.Series, qqq: pd.Series) -> dict:
    a = _compute_alpha_metrics(list(curve.values), list(curve.index), qqq, "1d")
    years = (curve.index[-1] - curve.index[0]).days / 365.25
    return {
        "cagr": (curve.iloc[-1] / curve.iloc[0]) ** (1 / years) - 1,
        "alpha": a["alpha"],
        "t": a["alpha_t"],
        "beta": a["beta"],
        "corr": a["benchmark_corr"],
        "max_dd": (curve / curve.cummax() - 1).min(),
    }


def evaluate(name, rules, m, expiries, earnings, model, split) -> dict:
    curve, book = simulate(name, rules, m, expiries, earnings, model)
    qqq = m["qqq"]
    return {
        "rules": rules,
        "trades": book.trades,
        "full": metrics(curve, qqq),
        "H1": metrics(curve.loc[:split], qqq),
        "H2": metrics(curve.loc[split:], qqq),
    }


def _row(name: str, label: str, x: dict, trades="") -> str:
    return (
        f"| {name} | {label} | {x['cagr']:+.1%} | {x['alpha']:+.1%} | {x['t']:.2f} | {x['beta']:.2f} | "
        f"{x['corr']:.2f} | {x['max_dd']:.1%} | {trades} |"
    )


HEADER = "| Bot | Window | CAGR | Alpha/yr | t | Beta | Corr | Max DD | Trades |\n|" + "---|" * 9


# ------------------------------------------------------------------
# Walk-forward grids: economically motivated levers only
# ------------------------------------------------------------------


def grid(name: str) -> list:
    base = ORIGINAL[name]
    if name == "option_LeapCallBot":
        axes = {
            "delta": [0.6, 0.7, 0.8],
            "leverage": [0.75, 1.0],
            "max_leverage": [None, 1.25, 1.5],
            "max_iv_hv": [1.2, 99.0],
            "exit_buffer": [0.0, 0.03],
        }
    elif name == "option_CreditSpreadBot":
        # Round 2 adds `width` (in $ at REF_PRICE: 3% / 6% / 10% of spot): a narrow
        # spread's long wing buys back most of the vol the short leg sells, so it
        # has almost no net vega with which to collect the variance premium.
        # min_iv_hv is pinned at 1.0 to keep the grid the same size.
        axes = {
            "sides": ["trend", "bull"],
            "width": [10.0, 20.0, 35.0],
            "short_delta": [0.20, 0.30],
            "target_dte": [35, 60],
            "take_profit": [0.5, 0.75],
            "stop_loss": [2.0, 99.0],
            "exit_dte": [7, 21],
            "min_iv_hv": [1.0],
        }
    elif name == "option_IronCondorBot":
        axes = {
            "width": [10.0, 20.0, 35.0],
            "short_delta": [0.16, 0.20],
            "call_delta": [None, 0.10],
            "target_dte": [35, 60],
            "take_profit": [0.5, 0.75],
            "stop_loss": [2.0, 99.0],
            "exit_dte": [7, 21],
            "max_adx": [25.0],
            "min_iv_hv": [1.15],
        }
    else:
        return []  # the catalyst bot: ~10 trades in 14 years is nothing to fit
    keys = list(axes)
    return [replace(base, **dict(zip(keys, combo, strict=True))) for combo in itertools.product(*axes.values())]


def _diff(rules, base) -> str:
    changed = {k: v for k, v in vars(rules).items() if vars(base)[k] != v}
    return ", ".join(f"{k}={v}" for k, v in changed.items()) or "(live defaults)"


def tune(m, expiries, earnings, model, split, bots: list[str]) -> None:
    for name in bots:
        combos = grid(name)
        results = Parallel(n_jobs=-1)(delayed(evaluate)(name, r, m, expiries, earnings, model, split) for r in combos)
        live = evaluate(name, ORIGINAL[name], m, expiries, earnings, model, split)
        ranked = sorted(results, key=lambda x: x["H1"]["t"], reverse=True)
        top10_h2 = np.mean([x["H2"]["t"] for x in ranked[:10]])
        share_pos = np.mean([x["H2"]["t"] > 0 for x in results])
        print(f"\n### {name}: {len(combos)} combos, picked on H1 alpha t, judged on H2")
        print(
            f"H2 t: original {live['H2']['t']:.2f}; mean of H1 top-10 {top10_h2:.2f}; share of grid with H2 t>0 {share_pos:.0%}"
        )
        print(HEADER)
        print(_row("original rules", "H1", live["H1"], live["trades"]))
        print(_row("original rules", "H2", live["H2"]))
        for i, x in enumerate(ranked[:5], 1):
            print(_row(f"#{i} {_diff(x['rules'], ORIGINAL[name])}", "H1", x["H1"], x["trades"]))
            print(_row(f"#{i}", "H2", x["H2"]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skew", type=float, default=0.15)
    ap.add_argument("--tune", action="store_true")
    ap.add_argument("--bots", default="option_LeapCallBot,option_CreditSpreadBot,option_IronCondorBot")
    args = ap.parse_args()

    m, earnings = load_inputs()
    expiries = monthly_expiries(m.index[0].date(), m.index[-1].date())
    split = m.index[len(m) // 2]
    model = Model(skew=args.skew)
    print(f"Window {m.index[0].date()} -> {m.index[-1].date()}, split {split.date()}, skew {args.skew}")
    print(f"IV proxy mean {m['iv'].mean():.3f}; median IV/HV20 {(m['iv'] / m['hv20']).median():.2f}")

    if args.tune:
        tune(m, expiries, earnings, model, split, args.bots.split(","))
        return
    print("\n" + HEADER)
    for name, rules in LIVE.items():
        x = evaluate(name, rules, m, expiries, earnings, model, split)
        for label in ("full", "H1", "H2"):
            print(_row(name, label, x[label], x["trades"] if label == "full" else ""))
    bh = m["S"] / m["S"].iloc[0] * CAPITAL
    for label, part in (("full", bh), ("H1", bh.loc[:split]), ("H2", bh.loc[split:])):
        print(_row("AAPL buy & hold", label, metrics(part, m["qqq"]), "-" if label == "full" else ""))


if __name__ == "__main__":
    main()
