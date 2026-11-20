"""S4: backtest + walk-forward tuning of SignedFxCarryBot and FxMomentumLSBot (PLAN-carryblend.md).

Real data only (yfinance closes + FRED CSV rates); a missing ticker raises. Each grid cell runs
`backtest_bot` with the QQQ benchmark, then alpha stats are taken on the evaluated equity curve
(weekday rows) for the full window, H1 and H2 (split at the midpoint of evaluated bars).
Protocol: rank every cell on H1 alpha_t, judge the H1 winner and the defaults on H2.

Usage:
    POSTGRES_URI=x:y@127.0.0.1:1/z uv run python scripts/onetime_carryblend_tune.py
Writes:
    docs/backtests/carryblend-2026-11.md and /tmp/carryblend_cells.json (cell cache)
"""

from __future__ import annotations

import itertools
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("POSTGRES_URI", "x:y@127.0.0.1:1/z")

import pandas as pd
import yfinance as yf
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import tradingbot.utils.db as db

# Scratch SQLite so Bot rows never touch (or wait on) Postgres.
_eng = create_engine(
    f"sqlite:///{os.environ.get('RETUNE_SCRATCH', '/tmp')}/carryblend.db", connect_args={"check_same_thread": False}
)
db.Base.metadata.create_all(_eng)
db.SessionLocal = sessionmaker(bind=_eng, autocommit=False, autoflush=False)
db._schema_initialized = True

from tradingbot.fxmomentumlsbot import FX_ETFS, FxMomentumLSBot  # noqa: E402
from tradingbot.signedfxcarrybot import ALL_FX_PAIRS, SignedFxCarryBot  # noqa: E402
from tradingbot.utils.alpha_report import alpha_stats  # noqa: E402
from tradingbot.utils.backtest import backtest_bot  # noqa: E402

OUT_DOC = Path("docs/backtests/carryblend-2026-11.md")
CACHE = Path(os.environ.get("CACHE_DIR", "/tmp")) / "carryblend_cells.json"
START = pd.Timestamp("2006-07-01")
EVAL_START = pd.Timestamp("2007-09-01")  # after the 252-bar momentum warmup


def load_frames(tickers: list[str]) -> dict[str, pd.DataFrame]:
    raw = yf.download(tickers, start=str(START.date()), auto_adjust=True, progress=False)
    frames: dict[str, pd.DataFrame] = {}
    for t in tickers:
        sub = raw.xs(t, axis=1, level=1).dropna(subset=["Close"]) if isinstance(raw.columns, pd.MultiIndex) else raw
        if sub.empty:
            raise RuntimeError(f"no data for {t}")  # never drop a ticker silently
        df = pd.DataFrame(
            {
                "timestamp": pd.DatetimeIndex(sub.index).tz_localize(None).normalize(),
                "open": sub["Open"].to_numpy(),
                "high": sub["High"].to_numpy(),
                "low": sub["Low"].to_numpy(),
                "close": sub["Close"].to_numpy(),
                "volume": sub["Volume"].fillna(0.0).to_numpy(),
            }
        )
        frames[t] = df.reset_index(drop=True)
    return frames


def equity_returns(res: dict) -> pd.Series:
    eq = pd.Series(
        [p["v"] for p in res["equity_curve"]],
        index=pd.DatetimeIndex([pd.Timestamp(p["t"]) for p in res["equity_curve"]]).tz_localize(None).normalize(),
    )
    eq = eq[~eq.index.duplicated(keep="last")]
    return eq.pct_change().dropna()


def stats_row(r: pd.Series, q: pd.Series, rf: pd.Series | None = None) -> dict:
    r = r[r.index >= EVAL_START]
    if rf is not None:
        r = r + rf.reindex(r.index).fillna(0.0)
    r = r[r.index.dayofweek < 5]
    qq = q.reindex(r.index)
    mid = len(r) // 2
    out: dict = {}
    for name, sl in (("full", slice(None)), ("h1", slice(0, mid)), ("h2", slice(mid, None))):
        st = alpha_stats(r.iloc[sl], qq.iloc[sl])
        out[name] = None if st is None else {"alpha": st.alpha, "t": st.t, "beta": st.beta, "corr": st.corr, "n": st.n}
    eq = (1 + r).cumprod()
    out["maxdd"] = float((eq / eq.cummax() - 1).min())
    out["cagr"] = float(eq.iloc[-1] ** (252 / len(r)) - 1)
    return out


def run_cell(cls, params: dict, frames: dict, qqq_close: pd.Series, tickers: list[str]) -> pd.Series:
    bot = cls(**params)
    bot.tickers = list(tickers)
    res = backtest_bot(
        bot,
        data={t: frames[t].copy() for t in tickers},
        benchmark_close=qqq_close,
        save_to_db=False,
        save_results_to_db=False,
        record_trial=False,
        return_series=True,
    )
    return equity_returns(res)


def grid(param_grid: dict) -> list[dict]:
    keys = list(param_grid)
    return [dict(zip(keys, vals, strict=True)) for vals in itertools.product(*param_grid.values())]


def fmt(s: dict | None) -> str:
    return "n/a" if s is None else f"{s['alpha'] * 100:+.2f}% (t {s['t']:+.2f})"


def main() -> None:
    frames = load_frames([*ALL_FX_PAIRS, *FX_ETFS, "QQQ", "^IRX"])
    qqq_close = frames["QQQ"]["close"].set_axis(pd.DatetimeIndex(frames["QQQ"]["timestamp"]))
    q = qqq_close.pct_change().dropna()
    rf = (frames["^IRX"]["close"].set_axis(pd.DatetimeIndex(frames["^IRX"]["timestamp"])) / 100.0 / 252.0).dropna()

    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    results: dict[str, list[dict]] = {}
    bots = (
        ("SignedFxCarryBot", SignedFxCarryBot, list(ALL_FX_PAIRS), None),
        ("FxMomentumLSBot", FxMomentumLSBot, list(FX_ETFS), rf),  # ETF bot: parity basis credits T-bill on cash
    )
    for name, cls, tickers, parity_rf in bots:
        rows = []
        cells = grid(cls.param_grid)
        if name == "SignedFxCarryBot":  # extra markup sweep at the defaults
            cells.append({"k": 3, "gross": 1.0, "markup": 0.02})
        for i, params in enumerate(cells):
            key = f"{name}:{json.dumps(params, sort_keys=True)}"
            if key not in cache:
                print(f"[{name}] {i + 1}/{len(cells)} {params}", file=sys.stderr, flush=True)
                r = run_cell(cls, params, frames, qqq_close, tickers)
                cache[key] = stats_row(r, q)
                if parity_rf is not None:
                    cache[key + ":parity"] = stats_row(r, q, rf=parity_rf)
                CACHE.write_text(json.dumps(cache))
            rows.append({"params": params, "framework": cache[key], "parity": cache.get(key + ":parity")})
        results[name] = rows

    lines = ["# Signed FX carry and FX momentum: backtest and walk-forward tuning (2026-11)", ""]
    lines += [
        "Generated by `scripts/onetime_carryblend_tune.py` on real data (yfinance closes, FRED 3m rates lagged 25 days).",
        "Evaluated bars start 2007-09; H1/H2 are the two halves of evaluated weekday bars. Alpha is vs QQQ via",
        "`alpha_stats`. Protocol: rank every cell on H1 alpha t, judge the H1 winner and the defaults on H2.",
        "",
    ]
    for name, rows in results.items():
        basis = "parity" if rows[0]["parity"] is not None else "framework"
        defaults = (
            {"k": 3, "gross": 1.0, "markup": 0.0}
            if name == "SignedFxCarryBot"
            else {"window": 252, "k": 2, "gross": 1.0}
        )
        n_variants = len(rows)
        ranked = sorted(rows, key=lambda x: (x[basis] or x["framework"])["h1"]["t"], reverse=True)
        winner = ranked[0]
        base = next(r for r in rows if r["params"] == defaults)
        lines += [f"## {name}", "", f"Variants tried: **{n_variants}** (selection bias applies to every t below).", ""]
        lines += [
            "| params | basis | full alpha | beta | H1 alpha (t) | H2 alpha (t) | max DD |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in ranked:
            s = r[basis] or r["framework"]
            tag = " (default)" if r["params"] == defaults else (" (H1 winner)" if r is winner else "")
            lines.append(
                f"| {json.dumps(r['params'])}{tag} | {basis} | {fmt(s['full'])} | {s['full']['beta']:+.2f} | "
                f"{fmt(s['h1'])} | {fmt(s['h2'])} | {s['maxdd'] * 100:.1f}% |"
            )
        w, b = (winner[basis] or winner["framework"]), (base[basis] or base["framework"])
        beats = w["h2"]["t"] > b["h2"]["t"] and w["h2"]["t"] > 0
        edge = w["h2"]["t"] >= 2.0 and w["h1"]["alpha"] > 0
        lines += [
            "",
            f"- H1 winner {winner['params']}: H2 t {w['h2']['t']:+.2f} vs defaults {b['h2']['t']:+.2f} -> "
            f"{'ship tuned values' if beats else 'keep defaults'}.",
            f"- Verdict (computed): **{'edge' if edge else 'unproven'}** "
            f"(edge needs H2 t >= 2 and H1 alpha > 0; H2 t {w['h2']['t']:+.2f}, H1 alpha {w['h1']['alpha'] * 100:+.2f}%).",
            "",
        ]
        if name == "SignedFxCarryBot":
            lines += ["Swap markup sensitivity at k=3, gross=1.0 (real MT5 markup is unmeasured):", ""]
            lines += ["| markup %/yr | full alpha | H1 | H2 |", "|---|---|---|---|"]
            for m in (0.0, 0.005, 0.01, 0.02):
                r = next(x for x in rows if x["params"] == {"k": 3, "gross": 1.0, "markup": m})["framework"]
                lines.append(f"| {m * 100:.1f} | {fmt(r['full'])} | {fmt(r['h1'])} | {fmt(r['h2'])} |")
            lines.append("")
    OUT_DOC.write_text("\n".join(lines) + "\n")
    print(f"wrote {OUT_DOC}", file=sys.stderr)


if __name__ == "__main__":
    main()
