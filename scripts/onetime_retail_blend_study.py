"""Joint walk-forward study for the retail book (PLAN.md Part A, step A1).

Question: should eToro / MT5 / Collective2 copy InstitutionalFlowBot (alone or in
an inverse-vol blend), or does a plain "QQQ above its 200-day SMA, else cash"
book do the same job for free?

Offline, like onetime_institutional_flow_backtest.py: yfinance data held in
memory, bot rows in a scratch SQLite file, nothing touches Postgres.

Streams and baselines
  * InstitutionalFlowBot, shipped defaults (top 20, weekly Friday, QQQ gate),
    run once over 2019-01 .. today through the framework's `backtest_bot`
    (0.05% slippage per side, stage-2 no-trade band) and sliced into halves.
  * Companion streams: option_ImpliedSignalBot needs the production
    `option_quotes` table and EarningsInsiderTiltBot needs DB earnings/insider
    history (insider rows were untyped before 2026-09-22). Neither curve can be
    rebuilt from yfinance, so both are dropped and the blend has one leg.
  * Inverse-vol blend, weights fit on H1 daily returns only.
  * QQQ buy-and-hold; QQQ + SMA200 gate (signal at close t, return t+1, cash 0
    and a T-bill variant); equal-weight buy-and-hold of the same universe.

Windows: H1 = 2019-01-01 .. 2022-12-31 (fit), H2 = 2023-01-01 .. today (judged).

Usage:
    RETUNE_SCRATCH=<scratch dir> PYTHONPATH=. uv run python scripts/onetime_retail_blend_study.py
Results: docs/backtests/retail-blend-2026-11.md
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import warnings

import numpy as np
import pandas as pd

FETCH_START, START, SPLIT = "2017-01-01", "2019-01-01", "2023-01-01"
HALVES = {"H1": (START, SPLIT), "H2": (SPLIT, "2100-01-01")}
COST_BPS = 20.0  # all-in cost per side on traded notional for the cost column
BUILTIN_SLIPPAGE_BPS = 5.0  # what backtest_bot already charges InstitutionalFlowBot
SMA_WINDOW = 200


# --------------------------------------------------------------------------- #
#  Pure helpers (unit-tested in tests/test_retail_blend_study.py)              #
# --------------------------------------------------------------------------- #


def inverse_vol_weights(returns: pd.DataFrame, fit_end: str | pd.Timestamp) -> dict[str, float]:
    """Weights proportional to 1 / daily-return std, measured strictly before `fit_end`.

    Rows on or after `fit_end` never enter the estimate, so H2 cannot leak into
    the weights. A stream with no usable H1 variance gets weight 0.
    """
    fit = returns[returns.index < pd.Timestamp(fit_end)]
    vol = fit.std()
    inv = (1.0 / vol).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    inv[vol <= 0] = 0.0
    if inv.sum() <= 0:
        raise ValueError("no stream has positive H1 volatility")
    w = inv / inv.sum()
    return {str(k): float(v) for k, v in w.items()}


def blend_returns(returns: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """Daily-rebalanced blend of the stream returns at fixed weights."""
    cols = list(weights)
    w = pd.Series(weights)[cols]
    return returns[cols].fillna(0.0).mul(w, axis=1).sum(axis=1)


def gated_qqq(
    close: pd.Series, window: int = SMA_WINDOW, rf_daily: pd.Series | None = None
) -> pd.DataFrame:
    """'QQQ above its SMA, else cash' with no look-ahead.

    The signal is formed at close t (close_t > SMA_t, SMA over closes up to and
    including t) and earns the close-to-close return of t+1. Until the SMA has
    `window` closes the book is in cash.

    Columns: `signal` (decided at close t), `exposure` (held during bar t, i.e.
    the previous close's signal), `ret` (cash earns 0), `ret_tbill` (cash earns
    `rf_daily`), `switch` (1 on a bar whose exposure changed).
    """
    close = close.astype(float).sort_index()
    sma = close.rolling(window, min_periods=window).mean()
    signal = (close > sma).astype(float)  # NaN SMA compares False -> cash
    exposure = signal.shift(1).fillna(0.0)
    r = close.pct_change().fillna(0.0)
    ret = exposure * r
    rf = (
        rf_daily.reindex(close.index).ffill().fillna(0.0)
        if rf_daily is not None
        else pd.Series(0.0, index=close.index)
    )
    out = pd.DataFrame({"signal": signal, "exposure": exposure, "ret": ret, "ret_tbill": ret + (1 - exposure) * rf})
    out["switch"] = exposure.diff().abs().fillna(exposure.iloc[0] if len(exposure) else 0.0)
    return out


def curve_from_returns(r: pd.Series) -> pd.Series:
    return (1.0 + r.fillna(0.0)).cumprod()


def cagr(r: pd.Series) -> float:
    r = r.dropna()
    if len(r) == 0:
        return float("nan")
    return float((1.0 + r).prod() ** (252.0 / len(r)) - 1.0)


def max_dd(r: pd.Series) -> float:
    c = curve_from_returns(r.dropna())
    c = pd.concat([pd.Series([1.0]), c.reset_index(drop=True)])
    return float((c / c.cummax() - 1.0).min())


# --------------------------------------------------------------------------- #
#  Data and the InstitutionalFlowBot run                                       #
# --------------------------------------------------------------------------- #


def _install_offline_backends(scratch: str) -> None:
    """Scratch SQLite for bot rows + in-memory yfinance for market data."""
    os.environ.setdefault("POSTGRES_URI", "stub:stub@localhost:5432/stub")
    import yfinance as yf
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import tradingbot.utils.db as db
    from tradingbot.utils.data_service import DataService

    eng = create_engine(f"sqlite:///{scratch}/retail_blend.db", connect_args={"check_same_thread": False})
    db.Base.metadata.create_all(eng)
    db.SessionLocal = sessionmaker(bind=eng, autocommit=False, autoflush=False)
    db._schema_initialized = True

    cache: dict = {}

    def _yf(self, symbol, interval="1d", period="1y", save_to_db=False, use_cache=True):
        key = (symbol, interval)
        if key not in cache:
            raw = yf.download(symbol, start=FETCH_START, interval=interval, auto_adjust=True, progress=False)
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)
            df = raw.dropna().reset_index()
            df.columns = [str(c).lower() for c in df.columns]
            df = df.rename(columns={"date": "timestamp", "datetime": "timestamp"})
            df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
            df["symbol"] = symbol
            cache[key] = df[["symbol", "timestamp", "open", "high", "low", "close", "volume"]]
        return cache[key].copy()

    DataService.get_yf_data = _yf


def _tbill_daily() -> pd.Series:
    """13-week T-bill discount yield (^IRX, percent) as a daily simple rate."""
    import yfinance as yf

    raw = yf.download("^IRX", start=FETCH_START, auto_adjust=False, progress=False)
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    s = raw["Close"].dropna()
    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
    return s / 100.0 / 252.0


def run_institutional_flow(scratch: str) -> dict:
    """Run the shipped InstitutionalFlowBot once over START..today and rebuild its book."""
    _install_offline_backends(scratch)
    from tradingbot.institutionalflowbot import BENCHMARK, InstitutionalFlowBot
    from tradingbot.utils.backtest import _close_series, backtest_bot

    probe = InstitutionalFlowBot()
    frames, excluded = {}, []
    cutoff = pd.Timestamp(FETCH_START) + pd.Timedelta(days=10)
    for t in probe.tickers:
        try:
            f = probe.getYFDataWithTA(symbol=t, interval="1d", period="max")
        except Exception as exc:  # noqa: BLE001 — report and drop, like the inst-flow study
            excluded.append(f"{t} (fetch: {exc})")
            continue
        if f.empty or f["timestamp"].min() > cutoff:
            excluded.append(t)
            continue
        frames[t] = f
    universe = [t for t in frames if t != BENCHMARK]
    qqq_close = _close_series(frames[BENCHMARK])
    data = {t: f[f.timestamp >= START].reset_index(drop=True) for t, f in frames.items()}

    bot = InstitutionalFlowBot()
    bot.tickers = [*universe, BENCHMARK]
    r = backtest_bot(
        bot, data=data, benchmark_close=qqq_close, save_to_db=False, save_results_to_db=False, return_series=True
    )
    eq = pd.Series(
        [p["v"] for p in r["equity_curve"]],
        index=pd.DatetimeIndex([pd.Timestamp(p["t"]) for p in r["equity_curve"]]).tz_localize(None).normalize(),
    )
    eq = eq[~eq.index.duplicated(keep="last")]

    closes = pd.DataFrame({t: _close_series(frames[t]) for t in universe})
    closes.index = pd.DatetimeIndex(closes.index).normalize()
    closes = closes[~closes.index.duplicated(keep="last")].reindex(eq.index).ffill()

    trades = pd.DataFrame(r["trades"])
    trades["t"] = pd.DatetimeIndex(pd.to_datetime(trades["t"], utc=True)).tz_convert(None).normalize()
    trades["signed"] = np.where(trades["side"] == "buy", 1.0, -1.0) * trades["qty"]
    trades["notional"] = trades["qty"] * trades["price"]
    qty = trades.pivot_table(index="t", columns="symbol", values="signed", aggfunc="sum").reindex(eq.index)
    qty = qty.fillna(0.0).cumsum()
    held_value = (qty * closes.reindex(columns=qty.columns)).sum(axis=1)
    cash_share = (1.0 - held_value / eq).clip(lower=0.0, upper=1.0)
    turnover = trades.groupby("t")["notional"].sum().reindex(eq.index).fillna(0.0) / eq
    n_trades = trades.groupby("t").size().reindex(eq.index).fillna(0).astype(int)

    # EW buy-and-hold of the same universe (rebased per half later from daily returns of the
    # buy-and-hold *value*, so each half is its own fresh equal-weight purchase).
    return {
        "equity": eq,
        "cash_share": cash_share,
        "turnover": turnover,
        "n_trades": n_trades,
        "closes": closes,
        "qqq_close": pd.Series(qqq_close.to_numpy(), index=pd.DatetimeIndex(qqq_close.index).normalize()),
        "universe": universe,
        "excluded": excluded,
        "framework_alpha": {k: r.get(k) for k in ("alpha", "alpha_t", "beta", "benchmark_corr")},
    }


def ew_hold_returns(closes: pd.DataFrame, lo: str, hi: str) -> pd.Series:
    """Equal-weight buy-and-hold bought at the first close of [lo, hi)."""
    c = closes[(closes.index >= lo) & (closes.index < hi)].dropna(axis=1, how="any")
    value = (c / c.iloc[0]).mean(axis=1)
    return value.pct_change().dropna()


# --------------------------------------------------------------------------- #
#  Report                                                                      #
# --------------------------------------------------------------------------- #


def _stats(r: pd.Series, bench: pd.Series):
    from tradingbot.utils.alpha_report import alpha_stats

    return alpha_stats(r, bench)


def half_row(
    r: pd.Series,
    r_cost: pd.Series,
    r_tbill: pd.Series,
    qqq: pd.Series,
    ew: pd.Series,
    cash: pd.Series,
    trades: pd.Series,
) -> dict:
    vq, ve = _stats(r, qqq), _stats(r, ew)
    weeks = len(r) / 5.0
    return {
        "cagr": cagr(r),
        "maxdd": max_dd(r),
        "alpha_qqq": vq.alpha if vq else None,
        "t_qqq": vq.t if vq else None,
        "beta": vq.beta if vq else None,
        "corr": vq.corr if vq else None,
        "alpha_ew": ve.alpha if ve else None,
        "t_ew": ve.t if ve else None,
        "cash_share": float(cash.mean()),
        "trades_per_week": float(trades.sum() / weeks) if weeks else 0.0,
        "cagr_20bp": cagr(r_cost),
        "maxdd_20bp": max_dd(r_cost),
        "cagr_tbill": cagr(r_tbill),
        "n_days": len(r),
    }


def main() -> None:
    warnings.filterwarnings("ignore")
    logging.disable(logging.WARNING)
    scratch = os.environ.get("RETUNE_SCRATCH", "/tmp")
    t0 = time.time()

    inst = run_institutional_flow(scratch)
    rf = _tbill_daily()
    eq = inst["equity"]
    idx = eq.index
    rf_d = rf.reindex(idx).ffill().fillna(0.0)
    qqq_close = inst["qqq_close"]
    qqq_full = qqq_close[~qqq_close.index.duplicated(keep="last")]
    qqq_r = qqq_full.pct_change().reindex(idx).fillna(0.0)

    # InstitutionalFlowBot: return t comes from the book held at close t-1.
    r_if = eq.pct_change().fillna(0.0)
    extra = (COST_BPS - BUILTIN_SLIPPAGE_BPS) / 1e4
    r_if_cost = r_if - extra * inst["turnover"]
    cash_prev = inst["cash_share"].shift(1).fillna(1.0)
    r_if_tbill = r_if + cash_prev * rf_d

    # Gated QQQ, SMA warm from the 2017 history.
    g = gated_qqq(qqq_full, SMA_WINDOW, rf).reindex(idx)
    r_g, r_g_tbill = g["ret"], g["ret_tbill"]
    r_g_cost = r_g - (COST_BPS / 1e4) * g["switch"]

    # Blend: inverse-vol over the reproducible component streams, fit on H1 only.
    streams = pd.DataFrame({"InstitutionalFlowBot": r_if})
    weights = inverse_vol_weights(streams[streams.index >= START], SPLIT)
    r_bl = blend_returns(streams, weights)
    r_bl_cost = blend_returns(pd.DataFrame({"InstitutionalFlowBot": r_if_cost}), weights)
    r_bl_tbill = blend_returns(pd.DataFrame({"InstitutionalFlowBot": r_if_tbill}), weights)

    zeros = pd.Series(0.0, index=idx)
    out: dict = {
        "universe_size": len(inst["universe"]),
        "excluded": inst["excluded"],
        "weights": weights,
        "dropped": {
            "option_ImpliedSignalBot": "needs production option_quotes (Postgres); no offline cache exists",
            "EarningsInsiderTiltBot": "needs DB earnings/insider history; insider rows untyped before 2026-09-22",
        },
        "framework_full_alpha": inst["framework_alpha"],
        "halves": {},
    }
    for half, (lo, hi) in HALVES.items():
        m = (idx >= lo) & (idx < hi)
        # Drop the first bar of H1: it is the cash day the backtest starts on.
        sl = idx[m][1:] if half == "H1" else idx[m]
        ew = ew_hold_returns(inst["closes"], lo, hi).reindex(sl).fillna(0.0)
        q = qqq_r.reindex(sl)
        n_names = inst["closes"].shape[1]
        ew_trades = pd.Series(0, index=sl)
        ew_trades.iloc[0] = n_names
        qqq_trades = pd.Series(0, index=sl)
        qqq_trades.iloc[0] = 1
        rows = {
            "InstitutionalFlowBot": half_row(
                r_if[sl], r_if_cost[sl], r_if_tbill[sl], q, ew, inst["cash_share"][sl], inst["n_trades"][sl]
            ),
            "Inverse-vol blend": half_row(
                r_bl[sl], r_bl_cost[sl], r_bl_tbill[sl], q, ew, inst["cash_share"][sl], inst["n_trades"][sl]
            ),
            "QQQ": half_row(q, q - (COST_BPS / 1e4) * qqq_trades, q, q, ew, zeros[sl], qqq_trades),
            "QQQ + SMA200 gate": half_row(
                r_g[sl], r_g_cost[sl], r_g_tbill[sl], q, ew, 1 - g["exposure"][sl], g["switch"][sl]
            ),
            "EW S&P 100 hold": half_row(
                ew, ew - (COST_BPS / 1e4) * (ew_trades / n_names), ew, q, ew, zeros[sl], ew_trades
            ),
        }
        out["halves"][half] = {"start": str(sl[0].date()), "end": str(sl[-1].date()), "rows": rows}

    def ships(name: str, cagr_key: str = "cagr", dd_key: str = "maxdd") -> dict:
        h1, h2 = out["halves"]["H1"]["rows"], out["halves"]["H2"]["rows"]
        b, gq = h2[name], h2["QQQ + SMA200 gate"]
        checks = {
            "h2_cagr_ge_15": b[cagr_key] >= 0.15,
            "h2_maxdd_ge_-30": b[dd_key] >= -0.30,
            "alpha_vs_ew_pos_h1": (h1[name]["alpha_ew"] or -1) > 0,
            "alpha_vs_ew_pos_h2": (b["alpha_ew"] or -1) > 0,
            "not_beaten_by_gated_qqq": not (gq[cagr_key] > b[cagr_key] and gq[dd_key] > b[dd_key]),
        }
        return {"checks": checks, "ship": all(checks.values())}

    out["verdict"] = {
        "blend": ships("Inverse-vol blend"),
        "institutional_flow_alone": ships("InstitutionalFlowBot"),
        "blend_at_20bp": ships("Inverse-vol blend", "cagr_20bp", "maxdd_20bp"),
    }
    with open(os.path.join(scratch, "retail_blend.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=float)
    print(json.dumps(out, indent=1, default=float))
    print(f"done in {time.time() - t0:.0f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
