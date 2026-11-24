"""
Forward paper prop-firm challenges.

`prop_challenge.py` answers "would this have passed?" on history. This module answers
it going forward: it replays the live sleeve returns recorded in portfolio_worth since a
start date through a firm's rules at a fixed leverage, so the answer arrives one real
trading day at a time. Nothing trades. The state is a pure function of portfolio_worth
and the start date, so the daily job is idempotent and a missed day is repaired by the
next run.

Each sim has a QQQ twin at the same leverage and preset. A pass that the twin also makes
is volatility, not edge.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from tradingbot.utils.alpha_report import MAX_GAP_DAYS, weekday_levels
from tradingbot.utils.prop_challenge import PRESETS, FirmPreset, lever, run_funded, walk_phase

logger = logging.getLogger(__name__)

DEFAULT_START = date(2026, 11, 24)
DEFAULT_RF_DAILY = 0.04 / 252.0
BENCHMARK = "Benchmark_QQQ"

# Live C2 blend (values.yaml liveTrade.collective2XauZerifine.botWeights; docs/backtests/c2-blend-2026-09.md).
BLEND_WEIGHTS = {"TARegimeMultiAssetBot": 0.39, "TSMOMTrendBot": 0.24, "GoldenButterflyMomBot": 0.37}
SOURCES: dict[str, dict[str, float]] = {"blend": BLEND_WEIGHTS, "qqq": {BENCHMARK: 1.0}}


@dataclass(frozen=True)
class SimSpec:
    name: str
    preset: str
    leverage: float
    source: str  # key of SOURCES


# Leverage per preset is the one the H1 walk-forward picked for the blend
# (docs/backtests/prop-challenge-2026-11.md). QQQ twins share it.
_LEVERAGE = {"ftmo_2step_100k": 4.0, "the5ers_hs_100k": 4.0, "topstep_50k": 2.0}
SIMS: tuple[SimSpec, ...] = tuple(
    SimSpec(f"{src}_{preset}_{lev:g}x", preset, lev, src) for src in SOURCES for preset, lev in _LEVERAGE.items()
)


@dataclass(frozen=True)
class SimState:
    stage: str  # "phase 1", "phase 2", ... or "funded"
    status: str  # "running" | "breached" | "funded"
    trading_days: int
    stage_day: int
    equity: float | None  # stage units, 1.0 = the stage's starting balance
    limit_equity: float | None
    target_equity: float | None
    cum_return: float  # compounded levered return since the start, ignoring withdrawals
    payouts_usd: float


def source_returns(
    worth: dict[str, pd.Series], weights: dict[str, float], since: date | pd.Timestamp | None = None
) -> pd.Series:
    """
    Weighted daily returns of a basket of bots, rebalanced daily like the backtests.

    Weekend rows are dropped and a return that spans more than MAX_GAP_DAYS (a recorder
    outage, not a day's move) is dropped. Raises LookupError if a bot has no series.
    """
    missing = [b for b in weights if b not in worth]
    if missing:
        raise LookupError(f"no portfolio_worth series for {missing}")
    levels = pd.concat({b: weekday_levels(worth[b]) for b in weights}, axis=1, join="inner")
    rets = levels.pct_change()
    gap = levels.index.to_series().diff().dt.days
    rets = rets[gap <= MAX_GAP_DAYS].replace([np.inf, -np.inf], np.nan).dropna()
    out = sum(rets[b] * w for b, w in weights.items())
    if since is not None:
        out = out[out.index >= pd.Timestamp(since)]
    return out


def rf_daily(irx: pd.Series | None, index: pd.DatetimeIndex) -> np.ndarray:
    """Daily risk-free rate on `index` from ^IRX (annual percent), or a flat 4%/yr if it is unavailable."""
    if irx is None or irx.dropna().empty:
        return np.full(len(index), DEFAULT_RF_DAILY)
    s = irx.dropna() / 100.0 / 252.0
    return s.reindex(index, method="ffill").bfill().fillna(DEFAULT_RF_DAILY).to_numpy(dtype=float)


def track(r_levered: np.ndarray, preset: FirmPreset) -> SimState:
    """State of a challenge after the levered daily returns so far."""
    r = np.asarray(r_levered, dtype=float)
    cum = float(np.prod(1.0 + r) - 1.0) if len(r) else 0.0
    cursor = 0
    for idx, rules in enumerate(preset.phases):
        w = walk_phase(r[cursor:], rules)
        cursor += w.days
        stage = f"phase {idx + 1}"
        if w.result == "breach":
            return SimState(stage, "breached", cursor, w.days, w.equity, w.limit, 1.0 + rules.profit_target, cum, 0.0)
        if w.result != "pass":
            return SimState(stage, "running", cursor, w.days, w.equity, w.limit, 1.0 + rules.profit_target, cum, 0.0)
    funded_r = r[cursor:]
    payout, funded_days, _ = run_funded(funded_r, preset)
    alive = funded_days == len(funded_r)
    return SimState(
        "funded", "funded" if alive else "breached", cursor + funded_days, funded_days, None, None, None, cum, payout
    )


def run_sim(spec: SimSpec, worth: dict[str, pd.Series], irx: pd.Series | None, start: date) -> SimState:
    """Replay one sim from `start` on the series recorded so far."""
    preset = PRESETS[spec.preset]
    r = source_returns(worth, SOURCES[spec.source], since=start)
    if r.empty:
        return track(np.array([]), preset)
    levered = lever(r.to_numpy(), spec.leverage, rf_daily(irx, r.index), preset.financing_markup)
    return track(levered, preset)


def latest_return_date(worth: dict[str, pd.Series], weights: dict[str, float]) -> date | None:
    """The most recent weekday on which every bot in the basket has a worth row."""
    series = [weekday_levels(worth[b]) for b in weights if b in worth]
    if len(series) < len(weights) or any(s.empty for s in series):
        return None
    return min(s.index[-1] for s in series).date()
