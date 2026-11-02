"""
Backtest the VWAPPullback and OpenRangeBreakout rules on QQQ 5-minute bars.

These bots are Pattern B (path-dependent), which the built-in backtester cannot run,
so this drives the same ``simulate_session`` the live bots replay, session by session.

Reports, with and without costs: trades, win rate, avg win/loss in R, expectancy,
alpha / t / beta / corr / max DD of the daily return series vs QQQ close-to-close
(`alpha_stats`, the CLAUDE.md method), first-half / second-half, and a random-entry
control at matched risk (the reel's "better than all 100 random versions" test).

yfinance serves ~60 days of 5m bars, so n is ~40 sessions: far too few for t >= 2.
Treat every number as "unproven". The live bots save their bars to `historic_data`,
which is what a longer test will eventually run on (`--from-db`).

Usage: PYTHONPATH=. uv run python scripts/onetime_intraday_orb_vwap_backtest.py
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np
import pandas as pd

from tradingbot.utils.alpha_report import alpha_stats
from tradingbot.utils.intraday_session import (
    IntradayRules,
    Trade,
    _extract_timestamps,
    session_bars,
    session_close_ts,
    session_open_ts,
    to_ny,
    walk_to_exit,
)
from tradingbot.utils.orb_rules import ORBRules
from tradingbot.utils.vwap_pullback_rules import VWAPPullbackRules

SLIPPAGE = 0.0005  # per side, the framework's ExecutionConfig default


def split_sessions(bars: pd.DataFrame) -> dict:
    """{NY date: that day's RTH bars}, skipping empty days."""
    ts = _extract_timestamps(bars)
    days = sorted({to_ny(t).date() for t in ts})
    out = {}
    for d in days:
        s = session_bars(bars, d)
        if not s.empty:
            out[d] = s.reset_index(drop=True)
    return out


def trade_return(trade: Trade, slippage: float) -> float:
    """Fractional return of an all-in round trip: buy at entry*(1+s), sell at exit*(1-s)."""
    return trade.exit * (1 - slippage) / (trade.entry * (1 + slippage)) - 1.0


def run_rules(sessions: dict, rules: IntradayRules) -> dict:
    """{date: Trade | None}. Sessions still in progress ('open') are dropped."""
    out = {}
    for d, s in sessions.items():
        t = rules.simulate_session(s).trade
        out[d] = None if (t is not None and t.exit_reason == "open") else t
    return out


def daily_returns(trades: dict, slippage: float) -> pd.Series:
    return pd.Series(
        {pd.Timestamp(d): (trade_return(t, slippage) if t else 0.0) for d, t in trades.items()}
    ).sort_index()


def max_drawdown(r: pd.Series) -> float:
    eq = (1 + r).cumprod()
    return float((eq / eq.cummax() - 1).min()) if len(eq) else 0.0


def qqq_daily_returns(sessions: dict) -> pd.Series:
    """Close-to-close QQQ returns from the last RTH bar of each session."""
    closes = pd.Series({pd.Timestamp(d): float(s["close"].iloc[-1]) for d, s in sessions.items()}).sort_index()
    return closes.pct_change().dropna()


@dataclass
class Summary:
    name: str
    trades: int
    win_rate: float
    avg_win_r: float
    avg_loss_r: float
    expectancy_r: float
    total_return: float
    alpha: float | None
    t: float | None
    beta: float | None
    corr: float | None
    max_dd: float


def summarize(name: str, trades: dict, bench: pd.Series, slippage: float) -> Summary:
    ts = [t for t in trades.values() if t is not None]
    r = np.array([t.r_multiple for t in ts])
    wins, losses = r[r > 0], r[r <= 0]
    rets = daily_returns(trades, slippage)
    # QQQ has no return on the first session, so align on its index.
    a = alpha_stats(rets.reindex(bench.index).fillna(0.0), bench)
    return Summary(
        name=name,
        trades=len(ts),
        win_rate=float((r > 0).mean()) if len(r) else 0.0,
        avg_win_r=float(wins.mean()) if len(wins) else 0.0,
        avg_loss_r=float(losses.mean()) if len(losses) else 0.0,
        expectancy_r=float(r.mean()) if len(r) else 0.0,
        total_return=float((1 + rets).prod() - 1),
        alpha=a.alpha if a else None,
        t=a.t if a else None,
        beta=a.beta if a else None,
        corr=a.corr if a else None,
        max_dd=max_drawdown(rets),
    )


def random_control(
    sessions: dict, trades: dict, target_r: float, force_after_open_min: float | None, draws: int, seed: int = 0
) -> float:
    """
    Share of random-entry runs that the strategy beats on total R. Each traded day gets
    a random entry bar after the opening range with the *same risk (stop distance)* and
    the same target_r, walked to the same forced exit.
    """
    rng = np.random.default_rng(seed)
    strategy_total = sum(t.r_multiple for t in trades.values() if t)
    totals = []
    for _ in range(draws):
        total = 0.0
        for d, t in trades.items():
            if t is None:
                continue
            s = sessions[d]
            open_ts = session_open_ts(s)
            force = (
                open_ts + pd.Timedelta(minutes=force_after_open_min)
                if force_after_open_min
                else session_close_ts(s) - pd.Timedelta(minutes=10)
            )
            ts = _extract_timestamps(s)
            ok = [
                i
                for i in range(len(s) - 1)
                if to_ny(ts.iloc[i]) >= open_ts + pd.Timedelta(minutes=15) and to_ny(ts.iloc[i]) < force
            ]
            if not ok:
                continue
            i = int(rng.choice(ok))
            entry = float(s["close"].iloc[i])
            risk = (t.entry - t.stop) / t.entry * entry
            total += walk_to_exit(s, i, entry - risk, entry + target_r * risk, force).r_multiple
        totals.append(total)
    return float((np.array(totals) < strategy_total).mean())


def fetch_5m(symbol: str = "QQQ") -> pd.DataFrame:
    import yfinance as yf

    df = yf.download(symbol, interval="5m", period="60d", auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index().rename(columns=str.lower)
    df = df.rename(columns={"datetime": "timestamp", "date": "timestamp", "index": "timestamp"})
    return df[["timestamp", "open", "high", "low", "close", "volume"]]


def fetch_from_db(symbol: str = "QQQ") -> pd.DataFrame:
    from tradingbot.utils.data_service import DataService

    return DataService().get_yf_data(symbol, interval="5m", period="max", save_to_db=False, use_cache=False)


def report(
    label: str, sessions: dict, rules: IntradayRules, target_r: float, force_after_open_min: float | None, draws: int
) -> list[str]:
    bench = qqq_daily_returns(sessions)
    trades = run_rules(sessions, rules)
    lines = [f"### {label}  ({len(sessions)} sessions)"]
    days = sorted(sessions)
    halves = {"all": days, "first half": days[: len(days) // 2], "second half": days[len(days) // 2 :]}
    for tag, slip in (("no costs", 0.0), (f"slippage {SLIPPAGE:.2%}/side", SLIPPAGE)):
        for h, ds in halves.items():
            sub = {d: trades[d] for d in ds}
            b = bench[bench.index.isin([pd.Timestamp(d) for d in ds])]
            s = summarize(f"{tag} / {h}", sub, b, slip)
            alpha = "n/a" if s.alpha is None else f"{s.alpha:+.1%}/yr t {s.t:.2f} beta {s.beta:.2f} corr {s.corr:.2f}"
            lines.append(
                f"- {s.name}: {s.trades} trades, win {s.win_rate:.0%}, avg win {s.avg_win_r:+.2f}R, "
                f"avg loss {s.avg_loss_r:+.2f}R, expectancy {s.expectancy_r:+.3f}R, return {s.total_return:+.2%}, "
                f"alpha {alpha}, maxDD {s.max_dd:.2%}"
            )
    beaten = random_control(sessions, trades, target_r, force_after_open_min, draws)
    lines.append(f"- random-entry control ({draws} draws, matched risk): strategy total R beats {beaten:.0%} of them")
    return lines


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--from-db", action="store_true", help="read 5m bars from historic_data instead of yfinance")
    p.add_argument("--draws", type=int, default=100)
    args = p.parse_args()

    bars = fetch_from_db() if args.from_db else fetch_5m()
    sessions = split_sessions(bars)
    vwap, orb = VWAPPullbackRules(), ORBRules()
    out = report("VWAPPullbackBot defaults", sessions, vwap, vwap.target_r, None, args.draws)
    out += ["", *report("OpenRangeBreakoutBot defaults", sessions, orb, orb.target_r, orb.window_minutes, args.draws)]
    print("\n".join(out))


if __name__ == "__main__":
    main()
