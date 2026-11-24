"""
Prop-firm challenge simulator.

Pure numpy/pandas simulation of prop-firm evaluations and funded accounts.
Evaluates whether buying prop-firm challenges for a candidate return stream
yields positive expected value (EV) after fees, accounting for daily loss limits,
maximum drawdown (static and trailing), minimum trading days, consistency rules,
and profit split payouts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PhaseRules:
    """Rules defining a challenge evaluation phase or funded account risk limits."""

    profit_target: float  # e.g. 0.10 = +10% of initial balance
    max_loss: float  # e.g. 0.10; breach when equity <= limit
    trailing: str  # "static" | "eod_trailing" (limit trails highest EOD equity, capped at 1.0)
    daily_loss: float | None  # e.g. 0.05 of INITIAL balance vs prior close; None = no rule
    max_days: int | None  # trading days allowed; None = unlimited (sim caps at horizon)
    min_days: int = 0  # min trading days before a pass counts
    consistency: float | None = None  # best day profit <= consistency * total profit at pass time


@dataclass(frozen=True)
class FirmPreset:
    """Prop firm challenge configuration and payout terms."""

    name: str
    phases: tuple[PhaseRules, ...]  # evaluation phases in order (1 or 2)
    funded: PhaseRules  # funded-account breach rules (profit_target ignored)
    fee: float  # USD per attempt
    account_size: float  # USD notional
    profit_split: float  # trader share of funded profit, e.g. 0.8
    payout_every_days: int  # trading days between payout requests (e.g. 21)
    fee_refund_on_first_payout: bool
    financing_markup: float  # annual cost on notional ABOVE excess return (CFD swap markup; 0 for futures)
    note: str  # source/date of rules + "VERIFY before buying"
    monthly_fee: float = 0.0  # USD charged per started 21-day block during evaluation


PRESETS: dict[str, FirmPreset] = {
    "ftmo_2step_100k": FirmPreset(
        name="ftmo_2step_100k",
        phases=(
            PhaseRules(
                profit_target=0.10,
                max_loss=0.10,
                trailing="static",
                daily_loss=0.05,
                max_days=None,
                min_days=4,
                consistency=None,
            ),
            PhaseRules(
                profit_target=0.05,
                max_loss=0.10,
                trailing="static",
                daily_loss=0.05,
                max_days=None,
                min_days=4,
                consistency=None,
            ),
        ),
        funded=PhaseRules(
            profit_target=0.0,
            max_loss=0.10,
            trailing="static",
            daily_loss=0.05,
            max_days=None,
            min_days=0,
            consistency=None,
        ),
        fee=540.0,
        account_size=100_000.0,
        profit_split=0.8,
        payout_every_days=21,
        fee_refund_on_first_payout=True,
        financing_markup=0.02,
        note="FTMO 2-step 100k challenge rules as of 2026. VERIFY before buying.",
        monthly_fee=0.0,
    ),
    "topstep_50k": FirmPreset(
        name="topstep_50k",
        phases=(
            PhaseRules(
                profit_target=0.06,
                max_loss=0.04,
                trailing="eod_trailing",
                daily_loss=None,
                max_days=None,
                min_days=0,
                consistency=0.5,
            ),
        ),
        funded=PhaseRules(
            profit_target=0.0,
            max_loss=0.04,
            trailing="eod_trailing",
            daily_loss=None,
            max_days=None,
            min_days=0,
            consistency=None,
        ),
        fee=49.0,
        account_size=50_000.0,
        profit_split=0.9,
        payout_every_days=5,
        fee_refund_on_first_payout=False,
        financing_markup=0.0,
        note="Topstep 50k rules as of 2026. Monthly fee 49 USD. VERIFY before buying.",
        monthly_fee=49.0,
    ),
    "the5ers_hs_100k": FirmPreset(
        name="the5ers_hs_100k",
        phases=(
            PhaseRules(
                profit_target=0.08,
                max_loss=0.10,
                trailing="static",
                daily_loss=0.05,
                max_days=None,
                min_days=0,
                consistency=None,
            ),
        ),
        funded=PhaseRules(
            profit_target=0.0,
            max_loss=0.10,
            trailing="static",
            daily_loss=0.05,
            max_days=None,
            min_days=0,
            consistency=None,
        ),
        fee=495.0,
        account_size=100_000.0,
        profit_split=0.8,
        payout_every_days=21,
        fee_refund_on_first_payout=True,
        financing_markup=0.02,
        note="The 5ers High Stakes 100k rules as of 2026. VERIFY before buying.",
        monthly_fee=0.0,
    ),
}


@dataclass(frozen=True)
class PathOutcome:
    """Outcome of a single simulated challenge path."""

    result: str  # "pass" | "breach" | "timeout"
    phase_reached: int  # 0-based index of the phase where it ended
    days: int  # trading days used in evaluation
    payouts: float  # USD paid to trader in the funded stage (0 unless passed)
    funded_days: int  # days survived funded (0 unless passed)


@dataclass(frozen=True)
class ChallengeStats:
    """Summary statistics across simulated challenge attempts."""

    p_pass: float
    p_breach: float
    p_timeout: float
    median_days_to_pass: float | None
    mean_payout_if_pass: float
    ev_per_attempt: float  # -fee + E[payouts] + E[fee refund] - E[monthly fees]
    n_paths: int


def lever(
    r: np.ndarray,
    leverage: float,
    rf_daily: np.ndarray | float = 0.0,
    financing_markup: float = 0.0,
) -> np.ndarray:
    """
    Scale daily returns by leverage and subtract financing cost.

    Formula: L * r - L * rf_daily - L * financing_markup / 252.

    Inputs are total-return series (ETFs carry cash yield), and a prop sim
    account pays excess return on notional: futures embed financing, CFDs charge swap.
    Pass rf_daily = 0.0 only when caller lacks a risk-free rate series.
    """
    r_arr = np.asarray(r, dtype=float)
    L = float(leverage)
    return L * r_arr - L * rf_daily - (L * financing_markup / 252.0)


def run_phase(r: np.ndarray, rules: PhaseRules) -> tuple[str, int]:
    """
    Walk days starting equity 1.0.

    Breach checked before target on same day (conservative ordering).
    Daily loss: equity_t - equity_{t-1} <= -daily_loss (in initial-balance units).
    Max loss:
      - static: equity_t <= 1 - max_loss
      - eod_trailing: limit = min(peak_eod - max_loss, 1.0), equity_t <= limit
    Pass requires profit_target, min_days, and consistency rule (max daily profit <= consistency * total profit).
    If consistency fails, keep trading.
    Timeout if max_days reached or path ends without pass or breach.

    Returns:
        (result, days_used) where result is 'pass', 'breach', or 'timeout'.
    """
    r_arr = np.asarray(r, dtype=float)
    if len(r_arr) == 0:
        return ("timeout", 0)

    equity = 1.0
    peak_eod = 1.0
    daily_profits: list[float] = []

    for t in range(len(r_arr)):
        day = t + 1
        ret = float(r_arr[t])
        prior_equity = equity
        equity = prior_equity * (1.0 + ret)
        daily_pnl = equity - prior_equity
        daily_profits.append(daily_pnl)

        # 1. Breach checks (breach beats pass on the same day)
        if rules.daily_loss is not None and daily_pnl <= -rules.daily_loss + 1e-12:
            return ("breach", day)

        if rules.trailing == "static":
            limit = 1.0 - rules.max_loss
            if equity <= limit + 1e-12:
                return ("breach", day)
        elif rules.trailing == "eod_trailing":
            limit = min(peak_eod - rules.max_loss, 1.0)
            if equity <= limit + 1e-12:
                return ("breach", day)
        else:
            raise ValueError(f"Unknown trailing mode: {rules.trailing!r}")

        # 2. Pass checks
        total_profit = equity - 1.0
        target_reached = total_profit >= rules.profit_target - 1e-12
        min_days_reached = day >= rules.min_days

        consistency_ok = True
        if rules.consistency is not None:
            max_daily_profit = max(daily_profits)
            if max_daily_profit > rules.consistency * total_profit + 1e-12:
                consistency_ok = False

        if target_reached and min_days_reached and consistency_ok:
            return ("pass", day)

        # Update peak_eod for trailing limit
        if equity > peak_eod:
            peak_eod = equity

        # 3. Timeout checks
        if rules.max_days is not None and day >= rules.max_days:
            return ("timeout", day)

    return ("timeout", len(r_arr))


def run_funded(r: np.ndarray, preset: FirmPreset) -> tuple[float, int, bool]:
    """
    Simulate funded stage starting from fresh equity 1.0.

    Every payout_every_days, if equity > 1, payout:
        (equity - 1) * account_size * profit_split
    and reset equity and peak_eod to 1.0.
    Fee refund is paid once at the first payout if enabled.
    Stops at breach or path end. Breach forfeits unpaid profit.

    Returns:
        (payout_usd, days, refund_paid)
    """
    r_arr = np.asarray(r, dtype=float)
    if len(r_arr) == 0:
        return (0.0, 0, False)

    equity = 1.0
    peak_eod = 1.0
    total_payout_usd = 0.0
    refund_paid = False
    funded_days = 0

    rules = preset.funded

    for t in range(len(r_arr)):
        funded_days += 1
        ret = float(r_arr[t])
        prior_equity = equity
        equity = prior_equity * (1.0 + ret)
        daily_pnl = equity - prior_equity

        # Breach checks
        if rules.daily_loss is not None and daily_pnl <= -rules.daily_loss + 1e-12:
            return (total_payout_usd, funded_days, refund_paid)

        if rules.trailing == "static":
            limit = 1.0 - rules.max_loss
            if equity <= limit + 1e-12:
                return (total_payout_usd, funded_days, refund_paid)
        elif rules.trailing == "eod_trailing":
            limit = min(peak_eod - rules.max_loss, 1.0)
            if equity <= limit + 1e-12:
                return (total_payout_usd, funded_days, refund_paid)
        else:
            raise ValueError(f"Unknown trailing mode: {rules.trailing!r}")

        if equity > peak_eod:
            peak_eod = equity

        # Payout check every payout_every_days
        if preset.payout_every_days > 0 and funded_days % preset.payout_every_days == 0 and equity > 1.0:
            profit = equity - 1.0
            payout = profit * preset.account_size * preset.profit_split
            total_payout_usd += payout
            if preset.fee_refund_on_first_payout and not refund_paid:
                refund_paid = True
            # Reset equity and peak to 1.0 on withdrawal
            equity = 1.0
            peak_eod = 1.0

    return (total_payout_usd, funded_days, refund_paid)


def simulate_path(r: np.ndarray, preset: FirmPreset) -> PathOutcome:
    """
    Chains evaluation phases on consecutive slices, funded stage uses remaining days.
    """
    r_arr = np.asarray(r, dtype=float)
    day_cursor = 0

    for phase_idx, rules in enumerate(preset.phases):
        r_slice = r_arr[day_cursor:]
        if len(r_slice) == 0:
            return PathOutcome(
                result="timeout",
                phase_reached=phase_idx,
                days=day_cursor,
                payouts=0.0,
                funded_days=0,
            )

        outcome, days_used = run_phase(r_slice, rules)
        day_cursor += days_used

        if outcome == "breach":
            return PathOutcome(
                result="breach",
                phase_reached=phase_idx,
                days=day_cursor,
                payouts=0.0,
                funded_days=0,
            )
        elif outcome == "timeout":
            return PathOutcome(
                result="timeout",
                phase_reached=phase_idx,
                days=day_cursor,
                payouts=0.0,
                funded_days=0,
            )

    # Passed all evaluation phases; remaining days run funded stage
    r_funded = r_arr[day_cursor:]
    payout_usd, funded_days, _ = run_funded(r_funded, preset)

    return PathOutcome(
        result="pass",
        phase_reached=len(preset.phases) - 1,
        days=day_cursor,
        payouts=payout_usd,
        funded_days=funded_days,
    )


def block_bootstrap(
    r: np.ndarray,
    n_paths: int,
    horizon: int,
    block: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """
    Circular block bootstrap with given block size.

    Deterministic for a given rng. Returns array of shape (n_paths, horizon).
    """
    r_arr = np.asarray(r, dtype=float).ravel()
    N = len(r_arr)
    if N == 0 or n_paths == 0 or horizon == 0:
        return np.empty((n_paths, horizon), dtype=float)

    b = max(1, int(block))
    k_blocks = math.ceil(horizon / b)
    starts = rng.integers(0, N, size=(n_paths, k_blocks))
    offsets = np.arange(b)
    indices = (starts[:, :, None] + offsets[None, None, :]) % N
    path_indices = indices.reshape(n_paths, k_blocks * b)[:, :horizon]
    return r_arr[path_indices]


def historical_paths(r: np.ndarray, horizon: int, step: int = 1) -> np.ndarray:
    """
    Generate historical overlapping windows of length horizon.

    Every start index with a full horizon window, in real order (no resampling).
    Returns array of shape (n_windows, horizon).
    """
    r_arr = np.asarray(r, dtype=float).ravel()
    N = len(r_arr)
    if horizon > N or horizon <= 0:
        return np.empty((0, horizon), dtype=float)

    starts = list(range(0, N - horizon + 1, max(1, int(step))))
    return np.array([r_arr[s : s + horizon] for s in starts], dtype=float)


def evaluate(paths: np.ndarray, preset: FirmPreset) -> ChallengeStats:
    """
    Aggregates simulate_path over rows of paths.

    ev_per_attempt = -fee + mean(payouts) + P(refund paid) * fee - monthly fees
    (if preset.monthly_fee > 0, charge monthly_fee per started 21-day block during evaluation:
     ceil(days / 21) * monthly_fee).
    """
    paths_arr = np.asarray(paths, dtype=float)
    if paths_arr.ndim == 1:
        paths_arr = paths_arr[None, :]

    n_paths = len(paths_arr)
    if n_paths == 0:
        return ChallengeStats(
            p_pass=0.0,
            p_breach=0.0,
            p_timeout=0.0,
            median_days_to_pass=None,
            mean_payout_if_pass=0.0,
            ev_per_attempt=-preset.fee,
            n_paths=0,
        )

    outcomes = [simulate_path(paths_arr[i], preset) for i in range(n_paths)]

    n_pass = sum(1 for o in outcomes if o.result == "pass")
    n_breach = sum(1 for o in outcomes if o.result == "breach")
    n_timeout = sum(1 for o in outcomes if o.result == "timeout")

    p_pass = n_pass / n_paths
    p_breach = n_breach / n_paths
    p_timeout = n_timeout / n_paths

    passed_outcomes = [o for o in outcomes if o.result == "pass"]
    if passed_outcomes:
        median_days_to_pass = float(np.median([o.days for o in passed_outcomes]))
        mean_payout_if_pass = float(np.mean([o.payouts for o in passed_outcomes]))
    else:
        median_days_to_pass = None
        mean_payout_if_pass = 0.0

    mean_payouts = float(np.mean([o.payouts for o in outcomes]))

    if preset.fee_refund_on_first_payout:
        p_refund = float(np.mean([1.0 if o.payouts > 0 else 0.0 for o in outcomes]))
    else:
        p_refund = 0.0

    if preset.monthly_fee > 0:
        monthly_fees = [math.ceil(o.days / 21) * preset.monthly_fee for o in outcomes]
        mean_monthly_fee = float(np.mean(monthly_fees))
    else:
        mean_monthly_fee = 0.0

    ev_per_attempt = float(-preset.fee + mean_payouts + p_refund * preset.fee - mean_monthly_fee)

    return ChallengeStats(
        p_pass=p_pass,
        p_breach=p_breach,
        p_timeout=p_timeout,
        median_days_to_pass=median_days_to_pass,
        mean_payout_if_pass=mean_payout_if_pass,
        ev_per_attempt=ev_per_attempt,
        n_paths=n_paths,
    )


def verdict(bot: ChallengeStats, control: ChallengeStats, min_ev: float = 0.0) -> str:
    """
    Return verdict string comparing bot to control benchmark.

    - "no edge over QQQ control" if bot.ev_per_attempt <= control.ev_per_attempt
    - "negative EV" if bot.ev_per_attempt <= min_ev
    - "positive EV (unverified rules)" otherwise
    """
    if bot.ev_per_attempt <= control.ev_per_attempt:
        return "no edge over QQQ control"
    elif bot.ev_per_attempt <= min_ev:
        return "negative EV"
    else:
        return "positive EV (unverified rules)"
