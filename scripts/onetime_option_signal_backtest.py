"""Option-implied signals for a long-only stock book: research backtest.

Three published findings say option prices lead the stock:
  S1 skew       Xing, Zhang & Zhao (2010): a steep put smile (25-delta put IV
                over ATM) predicts underperformance. Low is better.
  S2 call-put   Cremers & Weinbaum (2010): calls priced rich against puts at
                the same strikes predict outperformance. High is better.
  S3 IV change  An, Ang, Bali & Cakici (2014): rising implied vol predicts
                underperformance. The 4-week change of ATM IV; falling is better.
The primary test is their composite: the mean of the three cross-sectional
z-scores (at least two present). The single signals are diagnostics.

Fixed before the result, no parameter search:
  - signal: each name's last vol_surface row in the week, taken on Friday;
  - trade at the next session's close (the chains are end-of-day), hold a week;
  - top 10 by score, equal weight, long-only; 5 bps a side on turnover;
  - bar: alpha t >= 2 against the equal-weight basket of the names with a
    signal that week, in BOTH halves (H1 to 2022-12, H2 from 2023-01). The
    universe is today's 50 largest S&P 100 names, a survivor list; the
    equal-weight basket of the same names carries the same bias, QQQ would not.

Reads vol_surface from the production DB (port-forward, POSTGRES_URI); needs
the derive backfill (python -m tradingbot.optionchainsnapshot --backfill) over
the imported DoltHub chains. Results: docs/backtests/option-signals-2026-10.md

  uv run python scripts/onetime_option_signal_backtest.py [--start 2019-02-01]
"""

import argparse
from datetime import date

import numpy as np
import pandas as pd
import yfinance as yf

from tradingbot.utils.alpha_report import alpha_stats
from tradingbot.utils.db import VolSurfaceSnapshot, get_db_session
from tradingbot.utils.universes import OPTION_CAPTURE_UNIVERSE

TOP_N = 10
COST = 0.0005  # per side, on turnover
IV_CHANGE_WEEKS = 4
STALE_DAYS = 4  # a name's row counts for the week only if it is this fresh on Friday
H1_END = "2022-12-31"
SIGNALS = ("composite", "skew", "call_put", "iv_change")


def load_surface(start: date) -> pd.DataFrame:
    cols = (
        VolSurfaceSnapshot.underlying,
        VolSurfaceSnapshot.snapshot_date,
        VolSurfaceSnapshot.atm_iv_30,
        VolSurfaceSnapshot.iv_25p_30,
        VolSurfaceSnapshot.cp_spread_30,
    )
    with get_db_session() as session:
        rows = session.query(*cols).filter(VolSurfaceSnapshot.snapshot_date >= start).all()
    frame = pd.DataFrame(rows, columns=["name", "day", "atm", "p25", "cp"])
    frame["day"] = pd.to_datetime(frame["day"])
    return frame[~frame["name"].isin(["SPY", "QQQ"])]


def weekly(frame: pd.DataFrame, col: str, fridays: pd.DatetimeIndex) -> pd.DataFrame:
    """Each name's value as of each Friday: its last row no more than STALE_DAYS old."""
    panel = frame.pivot_table(index="day", columns="name", values=col, aggfunc="last").sort_index()
    if panel.empty:  # a column that never filled: no signal, not a crash
        return pd.DataFrame(np.nan, index=fridays, columns=sorted(set(frame["name"])))
    daily = panel.reindex(pd.date_range(panel.index[0], fridays[-1], freq="D")).ffill(limit=STALE_DAYS)
    return daily.reindex(fridays)


def zscore(x: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional z per row; a row with fewer than TOP_N names gets none."""
    z = x.sub(x.mean(axis=1), axis=0).div(x.std(axis=1), axis=0)
    return z.where(x.notna().sum(axis=1) >= TOP_N, axis=0)


def scores(frame: pd.DataFrame, fridays: pd.DatetimeIndex) -> dict[str, pd.DataFrame]:
    atm, p25, cp = (weekly(frame, c, fridays) for c in ("atm", "p25", "cp"))
    out = {
        "skew": zscore(-(p25 - atm)),
        "call_put": zscore(cp),
        "iv_change": zscore(-(atm - atm.shift(IV_CHANGE_WEEKS))),
    }
    stacked = pd.concat(out.values(), keys=out.keys())
    present = stacked.notna().groupby(level=1).sum()
    out["composite"] = stacked.groupby(level=1).mean().where(present >= 2)
    return out


def book(score: pd.DataFrame, sessions: pd.DatetimeIndex) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(top-N weights, equal weights of every scored name), daily, effective the session after each Friday."""
    top = score.rank(axis=1, ascending=False, method="first") <= TOP_N
    top = top.astype(float).div(top.sum(axis=1).replace(0, np.nan), axis=0)
    scored = score.notna().astype(float)
    ew = scored.div(scored.sum(axis=1).replace(0, np.nan), axis=0)
    out = []
    for weights in (top, ew):
        # Friday's chains close the week; trade at the next session's close, earn from the one after.
        weights = weights.copy()
        weights.index = [sessions[sessions > f][0] if (sessions > f).any() else pd.NaT for f in weights.index]
        weights = weights[weights.index.notna()]
        daily = weights.reindex(sessions).ffill().fillna(0.0)
        out.append(daily.shift(1).fillna(0.0))
    return out[0], out[1]


def net_returns(weights: pd.DataFrame, rets: pd.DataFrame) -> pd.Series:
    names = weights.columns.intersection(rets.columns)
    gross = (weights[names] * rets[names].fillna(0.0)).sum(axis=1)
    turnover = weights[names].diff().abs().sum(axis=1).fillna(0.0)
    return gross - turnover * COST


def row(label: str, window: str, x: pd.Series, bench: pd.Series) -> str:
    s = alpha_stats(x, bench)
    eq = (1 + x).cumprod()
    dd = float((eq / eq.cummax() - 1).min())
    if s is None:
        return f"| {label} | {window} | n/a |"
    return f"| {label} | {window} | {s.alpha:+.1%} | {s.t:+.2f} | {s.beta:.2f} | {s.corr:.2f} | {dd:.1%} |"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2019-02-01")
    args = ap.parse_args()
    start = date.fromisoformat(args.start)

    frame = load_surface(start)
    names = sorted(set(frame["name"]))
    print(
        f"vol_surface: {len(frame)} rows, {len(names)} names, {frame['day'].min():%Y-%m-%d} -> {frame['day'].max():%Y-%m-%d}"
    )
    fill = frame.notna().mean()
    print(f"non-null: atm {fill['atm']:.0%}, 25d put {fill['p25']:.0%}, call-put {fill['cp']:.0%}")

    px = yf.download([*names, "QQQ"], start=str(start), auto_adjust=True, progress=False)["Close"]
    px.index = pd.DatetimeIndex(px.index).tz_localize(None).normalize()
    rets = px.pct_change()
    sessions = px.index
    fridays = pd.date_range(frame["day"].min(), frame["day"].max(), freq="W-FRI")

    per_signal = scores(frame, fridays)
    coverage = per_signal["composite"].notna().sum(axis=1)
    print(f"weeks: {len(fridays)}, scored names per week: median {coverage.median():.0f}, min {coverage.min():.0f}")

    print("\n| Signal | Window | Alpha/yr | t | Beta | Corr | Max DD |")
    print("|---|---|---|---|---|---|---|")
    verdict = {}
    for name in SIGNALS:
        top, ew = book(per_signal[name], sessions)
        strat, base = net_returns(top, rets), net_returns(ew, rets)
        live = top.sum(axis=1) > 0
        strat, base, qqq = strat[live], base[live], rets["QQQ"][live]
        ts = []
        for window, part in (("full", slice(None)), ("H1", slice(None, H1_END)), ("H2", slice(H1_END, None))):
            x, b = strat.loc[part], base.loc[part]
            print(row(f"{name} vs EW", window, x, b))
            if window != "full":
                s = alpha_stats(x, b)
                ts.append(s.t if s else 0.0)
        print(row(f"{name} vs QQQ", "full", strat, qqq))
        if name == "composite":
            print(row("EW basket vs QQQ", "full", base, qqq))
        verdict[name] = min(ts) >= 2.0
    print(f"\nPrimary (composite) passes the bar (t >= 2 vs EW in both halves): {verdict['composite']}")
    print("Diagnostics:", ", ".join(f"{k} {'pass' if v else 'fail'}" for k, v in verdict.items() if k != "composite"))
    missing = sorted(set(OPTION_CAPTURE_UNIVERSE) - set(names) - {"SPY", "QQQ"})
    if missing:
        print("No vol_surface rows for:", ", ".join(missing))


if __name__ == "__main__":
    main()
