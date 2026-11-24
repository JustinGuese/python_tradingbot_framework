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
    activation_fee: float = 0.0  # USD charged once when the evaluation is passed (e.g. Topstep XFA)
    max_payout_request: float | None = None  # gross USD cap per payout request; remainder stays in the account


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
        note=(
            "FTMO 2-step 100k, matched against ftmo.com/en/2-step-challenge on 2026-11-24: targets 10%/5%, 5% daily, "
            "10% max loss, 4 min days, unlimited time, fee refunded at first reward. Split is 80% (up to 90% "
            "per FTMO). Fee shown is the list price in EUR treated as USD. NOT modelled: the 14-day minimum before "
            "the first reward and the CFD instrument limits. VERIFY before buying."
        ),
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
                min_days=2,
                consistency=0.55,
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
        fee=0.0,
        account_size=50_000.0,
        profit_split=0.9,
        payout_every_days=5,
        fee_refund_on_first_payout=False,
        financing_markup=0.0,
        note=(
            "Topstep 50k Combine, read from help.topstep.com on 2026-11-24: target $3,000, end-of-day trailing "
            "$2,000 loss limit locking at the start balance, best day under 55% of target, 2 min days, $49/month, "
            "$149 activation on the Express account, 90% split, $2,000 cap per payout request. NOT modelled: the "
            "optional daily loss limit, the loss limit dropping to $0 after the first payout, the 40% consistency "
            "objective and 5-winning-day payout gate on the funded account, the $125 minimum payout, and the "
            "undocumented VPS policy. VERIFY before buying."
        ),
        monthly_fee=49.0,
        activation_fee=149.0,
        max_payout_request=2_000.0,
    ),
    "the5ers_hs_100k": FirmPreset(
        name="the5ers_hs_100k",
        phases=(
            PhaseRules(
                profit_target=0.10,
                max_loss=0.10,
                trailing="static",
                daily_loss=0.05,
                max_days=None,
                min_days=3,
                consistency=None,
            ),
            PhaseRules(
                profit_target=0.05,
                max_loss=0.10,
                trailing="static",
                daily_loss=0.05,
                max_days=None,
                min_days=3,
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
        fee=545.0,
        account_size=100_000.0,
        profit_split=0.8,
        payout_every_days=21,
        fee_refund_on_first_payout=True,
        financing_markup=0.02,
        note=(
            "The5ers High Stakes 100k, from search snippets of the5ers.com FAQs on 2026-11-24 (pages could not "
            "be fetched): two steps, unlimited time, 10% max loss, 5% daily, payout cap $4,000. Sources conflict "
            "on the step-1 target (8% or 10%; 10% used) and the fee ($545 from one promo). Minimum-days rule is "
            "three profitable days of 0.5%, approximated as 3 trading days. VERIFY before buying."
        ),
        monthly_fee=0.0,
        max_payout_request=4_000.0,
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


@dataclass(frozen=True)
class PhaseWalk:
    """State of one evaluation phase after walking the days it has had so far."""

    result: str  # "pass" | "breach" | "timeout" | "running" (data ended with nothing decided)
    days: int  # trading days walked
    equity: float  # equity at the last walked day (1.0 = starting balance)
    peak_eod: float  # highest end-of-day equity so far
    limit: float | None  # equity level that breaches the max-loss rule today (None before day 1)
    best_day: float  # best single-day profit so far, in starting-balance units


def walk_phase(r: np.ndarray, rules: PhaseRules) -> PhaseWalk:
    """
    Walk days starting equity 1.0 and return the full state, not just the verdict.

    Breach checked before target on same day (conservative ordering).
    Daily loss: equity_t - equity_{t-1} <= -daily_loss (in initial-balance units).
    Max loss:
      - static: equity_t <= 1 - max_loss
      - eod_trailing: limit = min(peak_eod - max_loss, 1.0), equity_t <= limit
    Pass requires profit_target, min_days, and consistency rule (max daily profit <= consistency * total profit).
    If consistency fails, keep trading.
    "timeout" if max_days is reached; "running" if the data ends with nothing decided.
    """
    r_arr = np.asarray(r, dtype=float)
    equity = 1.0
    peak_eod = 1.0
    limit: float | None = None
    best_day = 0.0

    for t in range(len(r_arr)):
        day = t + 1
        prior_equity = equity
        equity = prior_equity * (1.0 + float(r_arr[t]))
        daily_pnl = equity - prior_equity
        best_day = max(best_day, daily_pnl)

        # 1. Breach checks (breach beats pass on the same day)
        if rules.trailing == "static":
            limit = 1.0 - rules.max_loss
        elif rules.trailing == "eod_trailing":
            limit = min(peak_eod - rules.max_loss, 1.0)
        else:
            raise ValueError(f"Unknown trailing mode: {rules.trailing!r}")
        daily_breach = rules.daily_loss is not None and daily_pnl <= -rules.daily_loss + 1e-12
        if daily_breach or equity <= limit + 1e-12:
            return PhaseWalk("breach", day, equity, peak_eod, limit, best_day)

        # 2. Pass checks
        total_profit = equity - 1.0
        target_reached = total_profit >= rules.profit_target - 1e-12
        min_days_reached = day >= rules.min_days
        consistency_ok = rules.consistency is None or best_day <= rules.consistency * total_profit + 1e-12
        if target_reached and min_days_reached and consistency_ok:
            return PhaseWalk("pass", day, equity, peak_eod, limit, best_day)

        # Update peak_eod for trailing limit
        if equity > peak_eod:
            peak_eod = equity

        # 3. Timeout checks
        if rules.max_days is not None and day >= rules.max_days:
            return PhaseWalk("timeout", day, equity, peak_eod, limit, best_day)

    return PhaseWalk("running", len(r_arr), equity, peak_eod, limit, best_day)


def run_phase(r: np.ndarray, rules: PhaseRules) -> tuple[str, int]:
    """
    Verdict of one phase: ('pass' | 'breach' | 'timeout', days_used). See walk_phase for the rules.

    A path that ends with nothing decided is a 'timeout'.
    """
    w = walk_phase(r, rules)
    return ("timeout" if w.result == "running" else w.result, w.days)


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
            profit_usd = (equity - 1.0) * preset.account_size
            gross_usd = profit_usd if preset.max_payout_request is None else min(profit_usd, preset.max_payout_request)
            total_payout_usd += gross_usd * preset.profit_split
            if preset.fee_refund_on_first_payout and not refund_paid:
                refund_paid = True
            # Withdraw the requested amount; any profit above the cap stays in the account.
            equity -= gross_usd / preset.account_size
            peak_eod = equity

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

    ev_per_attempt = float(
        -preset.fee + mean_payouts + p_refund * preset.fee - mean_monthly_fee - p_pass * preset.activation_fee
    )

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
