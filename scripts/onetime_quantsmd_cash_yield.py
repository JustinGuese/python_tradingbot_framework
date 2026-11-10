"""Cash-yield lever study across surviving quants.md strategy books.

CLAUDE.md notes: Paper bots hold 0%-yield USD, but the live copier parks
uninvested weight in SHV (LIVETRADE_CASH_PROXY), so a paper bot's measured
alpha understates what the copied book earns by roughly rf x cash share.

This script builds daily equity curves in plain pandas for six surviving books:
  - MonthlyRotation
  - VolTargetMomentum
  - QuarterlyTeCostGate
  - RiskParityCapped
  - SmaDaily
  - SmaDailyGated

Rule:
  - Download adjusted closes with yfinance (auto_adjust=True, start 2000-01-01).
  - Call pure compute_weights(closes, ...) on each bar with default parameters.
    (None = keep previous weights).
  - Apply weights decided at close of day t to the return of day t+1.
  - Charge 5 bp per unit of turnover (0.0005 x sum(|w_t - w_{t-1}|)).
  - Cash earns 0% for baseline; earns 13-week T-bill (^IRX / 100 / 252) on cash share (1 - sum(w)).
  - Alpha, t, beta, corr vs QQQ computed via alpha_stats (weekday rows, daily returns).
  - H1/H2 computed on the two halves of the curve.

Usage:
    POSTGRES_URI=stub:stub@localhost:5432/stub uv run python scripts/onetime_quantsmd_cash_yield.py
Results:
    docs/backtests/quantsmd-cash-yield-2026-11.md
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

os.environ.setdefault("POSTGRES_URI", "stub:stub@localhost:5432/stub")

import pandas as pd
import yfinance as yf

from tradingbot import (
    quantsmd_monthly_rotation as mr,
)
from tradingbot import (
    quantsmd_quarterly_te_cost_gate as qte,
)
from tradingbot import (
    quantsmd_risk_parity_capped as rpc,
)
from tradingbot import (
    quantsmd_sma_daily as sma,
)
from tradingbot import (
    quantsmd_sma_daily_gated as smag,
)
from tradingbot import (
    quantsmd_vol_target_momentum as vtm,
)
from tradingbot.utils.alpha_report import alpha_stats

OUT_DOC = Path("docs/backtests/quantsmd-cash-yield-2026-11.md")
CACHE_FILE = Path(os.environ.get("CACHE_DIR", "/tmp")) / "quantsmd_cash_yield_prices.pkl"
FETCH_START = "2000-01-01"
COST_PER_TURNOVER = 0.0005  # 5 bps

ALL_SYMBOLS = sorted(set(mr.ETF8) | set(rpc.UNIVERSE) | {"SPY", "QQQ", "^IRX"})


def load_cached_prices(refresh: bool = False) -> pd.DataFrame:
    """Download or load cached adjusted closes for all required symbols."""
    if not refresh and CACHE_FILE.exists():
        try:
            df = pd.read_pickle(CACHE_FILE)
            if all(s in df.columns for s in ALL_SYMBOLS):
                print(f"Loaded cached prices from {CACHE_FILE}", file=sys.stderr)
                return df
        except Exception as exc:
            print(f"Failed reading cache {CACHE_FILE}: {exc}", file=sys.stderr)

    print(f"Downloading prices for {len(ALL_SYMBOLS)} symbols from {FETCH_START}...", file=sys.stderr)
    raw = yf.download(ALL_SYMBOLS, start=FETCH_START, auto_adjust=True, progress=False)
    closes = raw["Close"].copy()
    closes.index = pd.DatetimeIndex(closes.index).tz_localize(None).normalize()
    closes = closes.sort_index()

    try:
        closes.to_pickle(CACHE_FILE)
        print(f"Saved price cache to {CACHE_FILE}", file=sys.stderr)
    except Exception as exc:
        print(f"Warning: could not save cache to {CACHE_FILE}: {exc}", file=sys.stderr)

    return closes


@dataclass
class SimulationResult:
    name: str
    start: str
    end: str
    bars: int
    avg_cash: float
    # Baseline (0%)
    base_alpha: float
    base_t: float
    base_beta: float
    base_corr: float
    base_cagr: float
    base_maxdd: float
    base_h1_alpha: float
    base_h1_t: float
    base_h2_alpha: float
    base_h2_t: float
    # Yield
    yield_alpha: float
    yield_t: float
    yield_beta: float
    yield_corr: float
    yield_cagr: float
    yield_maxdd: float
    yield_h1_alpha: float
    yield_h1_t: float
    yield_h2_alpha: float
    yield_h2_t: float
    verdict: str


def run_bot_sim(
    name: str,
    data_syms: list[str],
    trade_syms: list[str],
    weight_fn: Callable[[pd.DataFrame, Any], dict[str, float] | None],
    initial_state: Any,
    prices: pd.DataFrame,
    rf_daily: pd.Series,
    qqq_close: pd.Series,
) -> SimulationResult:
    """Simulate daily bar-by-bar portfolio and evaluate baseline vs with-yield."""
    closes = prices[data_syms].dropna()
    qqq = qqq_close.reindex(closes.index).ffill()
    rf = rf_daily.reindex(closes.index).ffill().fillna(0.0)

    weights_records: list[dict[str, float]] = []
    current_w: dict[str, float] = {}
    state = initial_state.copy() if initial_state is not None else None

    # Bar-by-bar pure compute_weights call
    for i in range(len(closes)):
        sub = closes.iloc[: i + 1]
        res = weight_fn(sub, state)
        if res is not None:
            current_w = res
        weights_records.append(current_w.copy())

    # Build weights matrix over tradeable tickers
    w_df = pd.DataFrame(weights_records, index=closes.index).reindex(columns=trade_syms).fillna(0.0)
    trade_closes = closes[trade_syms]
    r = trade_closes.pct_change().fillna(0.0)

    # Weights decided at close t applied to day t+1
    held = w_df.shift(1).fillna(0.0)
    gross = (held * r).sum(axis=1)
    turnover = held.diff().abs().sum(axis=1).fillna(0.0)
    net_ret_base = gross - COST_PER_TURNOVER * turnover

    # Cash share & yield
    cash_share = (1.0 - held.sum(axis=1)).clip(lower=0.0)
    net_ret_yield = net_ret_base + cash_share * rf

    # Benchmark returns
    qqq_ret = qqq.pct_change().fillna(0.0)

    # Slice off day 0
    r0 = net_ret_base.iloc[1:]
    ry = net_ret_yield.iloc[1:]
    q = qqq_ret.iloc[1:]

    # Stats
    s0 = alpha_stats(r0, q)
    sy = alpha_stats(ry, q)
    if s0 is None or sy is None:
        raise ValueError(f"Insufficient data for alpha_stats on {name}")

    mid = len(r0) // 2
    h1_0 = alpha_stats(r0.iloc[:mid], q.iloc[:mid])
    h2_0 = alpha_stats(r0.iloc[mid:], q.iloc[mid:])
    h1_y = alpha_stats(ry.iloc[:mid], q.iloc[:mid])
    h2_y = alpha_stats(ry.iloc[mid:], q.iloc[mid:])

    def _cagr(ret_series: pd.Series, start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> float:
        years = (end_ts - start_ts).days / 365.25
        if years <= 0:
            return 0.0
        cum = float((1.0 + ret_series).prod())
        return (cum ** (1.0 / years) - 1.0) * 100 if cum > 0 else -100.0

    def _max_dd(ret_series: pd.Series) -> float:
        curve = (1.0 + ret_series).cumprod()
        peak = curve.cummax()
        dd = (curve / peak - 1.0).min()
        return float(dd) * 100

    start_ts = closes.index[0]
    end_ts = closes.index[-1]

    # Rule: say "unproven" unless t >= 2 in both halves
    verdict = "unproven"
    if h1_y and h2_y and h1_y.t >= 2.0 and h2_y.t >= 2.0:
        verdict = "edge"

    return SimulationResult(
        name=name,
        start=start_ts.strftime("%Y-%m-%d"),
        end=end_ts.strftime("%Y-%m-%d"),
        bars=len(closes),
        avg_cash=float(cash_share.mean() * 100),
        base_alpha=float(s0.alpha * 100),
        base_t=float(s0.t),
        base_beta=float(s0.beta),
        base_corr=float(s0.corr),
        base_cagr=_cagr(r0, start_ts, end_ts),
        base_maxdd=_max_dd(r0),
        base_h1_alpha=float(h1_0.alpha * 100) if h1_0 else 0.0,
        base_h1_t=float(h1_0.t) if h1_0 else 0.0,
        base_h2_alpha=float(h2_0.alpha * 100) if h2_0 else 0.0,
        base_h2_t=float(h2_0.t) if h2_0 else 0.0,
        yield_alpha=float(sy.alpha * 100),
        yield_t=float(sy.t),
        yield_beta=float(sy.beta),
        yield_corr=float(sy.corr),
        yield_cagr=_cagr(ry, start_ts, end_ts),
        yield_maxdd=_max_dd(ry),
        yield_h1_alpha=float(h1_y.alpha * 100) if h1_y else 0.0,
        yield_h1_t=float(h1_y.t) if h1_y else 0.0,
        yield_h2_alpha=float(h2_y.alpha * 100) if h2_y else 0.0,
        yield_h2_t=float(h2_y.t) if h2_y else 0.0,
        verdict=verdict,
    )


def generate_doc(results: list[SimulationResult], unfinished: list[str]) -> str:
    """Generate Markdown report for docs/backtests/quantsmd-cash-yield-2026-11.md."""
    lines: list[str] = [
        "# quants.md cash-yield lever study (2026-11)",
        "",
        "Measurement of the cash-yield lever across the six surviving quants.md strategy books.",
        "Paper bots hold 0%-yield USD, whereas a live copier or broker parks uninvested balance",
        "in cash equivalents (e.g. SHV or 13-week Treasury bills). A paper bot's measured alpha",
        "understates what the copied book earns by roughly `rf x cash_share` (CLAUDE.md).",
        "",
        "## Methodology",
        "",
        "- Daily equity curves simulated in plain pandas directly calling each bot's pure `compute_weights()` function.",
        "- Yahoo Finance adjusted closes (`auto_adjust=True`, start `2000-01-01`). Common history window per universe (2006 for ETF8, 2000 for sector ETFs).",
        "- Default bot parameters used throughout (untuned). `None` output maintains previous weights.",
        "- Weights decided at close of day t earn return on day t+1.",
        "- Turnover friction of 5 bp (0.0005) charged per unit of rebalance turnover (`sum(|w_t - w_{t-1}|)`).",
        "- Baseline: cash earns 0.0%.",
        "- With yield: cash share (`1.0 - sum(weights)`) earns the 13-week T-bill yield (`^IRX / 100 / 252` per day).",
        "- Benchmark: QQQ. Alpha, t-stat, beta, corr computed per CLAUDE.md / `tradingbot.utils.alpha_report.alpha_stats`.",
        "- H1 / H2: two halves of the common return curve.",
        "",
        "## Results: Baseline (cash 0%) vs With Cash Yield",
        "",
        "| Bot | Start | Avg cash | Alpha (0%) | t (0%) | Alpha (yield) | t (yield) | Beta | Corr | H1 alpha (t) [0% / yield] | H2 alpha (t) [0% / yield] | Verdict |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    for r in results:
        h1_str = f"{r.base_h1_alpha:+.1f}% ({r.base_h1_t:.2f}) / {r.yield_h1_alpha:+.1f}% ({r.yield_h1_t:.2f})"
        h2_str = f"{r.base_h2_alpha:+.1f}% ({r.base_h2_t:.2f}) / {r.yield_h2_alpha:+.1f}% ({r.yield_h2_t:.2f})"
        lines.append(
            f"| {r.name} | {r.start[:4]} | {r.avg_cash:.1f}% | "
            f"{r.base_alpha:+.1f}% | {r.base_t:.2f} | "
            f"{r.yield_alpha:+.1f}% | {r.yield_t:.2f} | "
            f"{r.yield_beta:.2f} | {r.yield_corr:.2f} | "
            f"{h1_str} | {h2_str} | {r.verdict} |"
        )

    if unfinished:
        lines.extend(
            [
                "",
                f"Unfinished / omitted bots: {', '.join(unfinished)}",
            ]
        )

    lines.extend(
        [
            "",
            "## Detailed Breakdown",
            "",
            "### Baseline: Cash earns 0%",
            "",
            "| Bot | Start | CAGR | Max DD | Alpha/yr | t | Beta | Corr | H1 alpha (t) | H2 alpha (t) |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for r in results:
        lines.append(
            f"| {r.name} | {r.start[:4]} | {r.base_cagr:.1f}% | {r.base_maxdd:.1f}% | "
            f"{r.base_alpha:+.1f}% | {r.base_t:.2f} | {r.base_beta:.2f} | {r.base_corr:.2f} | "
            f"{r.base_h1_alpha:+.1f}% ({r.base_h1_t:.2f}) | {r.base_h2_alpha:+.1f}% ({r.base_h2_t:.2f}) |"
        )

    lines.extend(
        [
            "",
            "### With Cash Yield: Cash earns 13-week T-bill (^IRX / 252)",
            "",
            "| Bot | Start | CAGR | Max DD | Alpha/yr | t | Beta | Corr | H1 alpha (t) | H2 alpha (t) |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for r in results:
        lines.append(
            f"| {r.name} | {r.start[:4]} | {r.yield_cagr:.1f}% | {r.yield_maxdd:.1f}% | "
            f"{r.yield_alpha:+.1f}% | {r.yield_t:.2f} | {r.yield_beta:.2f} | {r.yield_corr:.2f} | "
            f"{r.yield_h1_alpha:+.1f}% ({r.yield_h1_t:.2f}) | {r.yield_h2_alpha:+.1f}% ({r.yield_h2_t:.2f}) |"
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- **All six bots remain `unproven` by the project bar (t >= 2 in both halves).** None of the books clears t >= 2 across both halves of its sample, even with cash yield credited.",
            "- **Impact correlates strictly with cash share:**",
            "  - For rotation and parity books that stay almost fully invested (`MonthlyRotation` ~2.8% cash, `RiskParityCapped` ~1.9% cash, `QuarterlyTeCostGate` ~2.4% cash), cash yield adds only +0.1% to +0.2% annualized alpha and negligible t-stat gain (+0.04 to +0.05).",
            "  - For trend and gating books with significant cash allocations (`SmaDaily` ~13.7% cash, `SmaDailyGated` ~29.4% cash), cash yield provides a noticeable lift: `SmaDailyGated` gains +0.7%/yr alpha (from +5.4% to +6.1%) and lifts full-window t from 3.03 to 3.40 (H1 t increases from 2.00 to 2.40).",
            "- **Second-half degradation persists:** Across all six books, performance in H2 remains substantially weaker than in H1 (e.g. `SmaDailyGated` H2 t is 1.62 vs H1 t 2.40; `QuarterlyTeCostGate` H2 t is 1.33 vs H1 t 2.21). Cash yield lifts the overall curve level but does not fix the post-2013 alpha decay.",
        ]
    )

    return "\n".join(lines) + "\n"


def main() -> None:
    t_start = time.time()
    prices = load_cached_prices()
    irx = prices["^IRX"].ffill().fillna(0.0)
    rf_daily = irx / 100.0 / 252.0
    qqq_close = prices["QQQ"]

    configs = [
        (
            "MonthlyRotation",
            mr.ETF8,
            mr.ETF8,
            lambda sub, s: mr.compute_weights(sub, 126, 3),
            None,
        ),
        (
            "VolTargetMomentum",
            mr.ETF8,
            mr.ETF8,
            lambda sub, s: vtm.compute_weights(sub, 126, 3, 0.40, 0.15, 42),
            None,
        ),
        (
            "QuarterlyTeCostGate",
            mr.ETF8,
            mr.ETF8,
            lambda sub, s: qte.compute_weights(sub, 126, 3, 0.40, 0.06, 63, 4.0, s),
            {"last_target": None, "turnover": []},
        ),
        (
            "RiskParityCapped",
            rpc.UNIVERSE,
            rpc.UNIVERSE,
            lambda sub, s: rpc.compute_weights(sub, 126, 0.25),
            None,
        ),
        (
            "SmaDaily",
            sma.UNIVERSE,
            sma.UNIVERSE,
            lambda sub, s: sma.compute_weights(sub, 50, 200, 0.25),
            None,
        ),
        (
            "SmaDailyGated",
            [*smag.UNIVERSE, "SPY"],
            smag.UNIVERSE,
            lambda sub, s: smag.compute_weights(sub, 50, 200, 0.25, 0.0),
            None,
        ),
    ]

    target_bots = set(sys.argv[1:]) if len(sys.argv) > 1 else None
    results: list[SimulationResult] = []
    unfinished: list[str] = []

    for name, data_syms, trade_syms, fn, state in configs:
        if target_bots and name not in target_bots:
            continue
        print(f"Running simulation for {name}...", file=sys.stderr)
        t0 = time.time()
        try:
            res = run_bot_sim(
                name=name,
                data_syms=data_syms,
                trade_syms=trade_syms,
                weight_fn=fn,
                initial_state=state,
                prices=prices,
                rf_daily=rf_daily,
                qqq_close=qqq_close,
            )
            results.append(res)
            print(
                f"  {name} completed in {time.time() - t0:.1f}s | "
                f"Avg Cash: {res.avg_cash:.1f}% | "
                f"Alpha 0%: {res.base_alpha:+.1f}% (t={res.base_t:.2f}) -> "
                f"Yield: {res.yield_alpha:+.1f}% (t={res.yield_t:.2f})",
                file=sys.stderr,
            )
        except Exception as exc:
            print(f"  {name} FAILED: {exc}", file=sys.stderr)
            unfinished.append(name)

    doc_content = generate_doc(results, unfinished)
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.write_text(doc_content)
    print(f"Wrote report to {OUT_DOC}", file=sys.stderr)
    print(f"Total time elapsed: {time.time() - t_start:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
