"""
Tests for tradingbot.utils.prop_challenge.

Strictly tests the 5 scenarios specified in PLAN-propsim.md S2.
"""

from __future__ import annotations

import numpy as np
import pytest

from tradingbot.utils.prop_challenge import (
    PRESETS,
    PhaseRules,
    block_bootstrap,
    evaluate,
    lever,
    run_funded,
    run_phase,
)


def test_target_pass():
    """1. Target pass: [0.03, 0.03, 0.03, 0.02] on a phase with +10% target and no other limits

    ends in 'pass' on day 4. min_days=5 turns that into 'timeout'.
    """
    r = np.array([0.03, 0.03, 0.03, 0.02])

    rules_pass = PhaseRules(
        profit_target=0.10,
        max_loss=0.50,
        trailing="static",
        daily_loss=None,
        max_days=None,
        min_days=0,
    )
    outcome, days = run_phase(r, rules_pass)
    assert outcome == "pass"
    assert days == 4

    rules_timeout = PhaseRules(
        profit_target=0.10,
        max_loss=0.50,
        trailing="static",
        daily_loss=None,
        max_days=None,
        min_days=5,
    )
    outcome_to, days_to = run_phase(r, rules_timeout)
    assert outcome_to == "timeout"
    assert days_to == 4


def test_daily_loss_before_target():
    """2. Daily loss before target: a -6% day breaches a 5% daily-loss rule,

    even when the same path would later hit target. On the same day, breach beats pass.
    """
    # 2a: -6% day breaches on day 1 even though day 2 reaches target
    r_breach_first = np.array([-0.06, 0.20])
    rules = PhaseRules(
        profit_target=0.10,
        max_loss=0.50,
        trailing="static",
        daily_loss=0.05,
        max_days=None,
        min_days=0,
    )
    outcome, days = run_phase(r_breach_first, rules)
    assert outcome == "breach"
    assert days == 1

    # 2b: On the same day, breach beats pass.
    # Day 1: +15% profit, but min_days=2 prevents passing on day 1.
    # Day 2: -6% drop breaches daily loss 5% (drop is 1.15 * 0.06 = 0.069 > 0.05).
    # Remaining equity is 1.081 >= target 1.08 and day >= min_days=2,
    # but breach is checked before pass, so it must result in breach.
    r_same_day = np.array([0.15, -0.06])
    rules_same_day = PhaseRules(
        profit_target=0.08,
        max_loss=0.50,
        trailing="static",
        daily_loss=0.05,
        max_days=None,
        min_days=2,
    )
    outcome_same_day, days_same_day = run_phase(r_same_day, rules_same_day)
    assert outcome_same_day == "breach"
    assert days_same_day == 2


def test_eod_trailing():
    """3. EOD trailing: for [+0.03, -0.05] with max loss 0.04, eod_trailing breaches,

    but static does not. Once peak passes +4%, limit stops at 1.0 (no further trailing).
    """
    r = np.array([0.03, -0.05])

    rules_static = PhaseRules(
        profit_target=0.50,
        max_loss=0.04,
        trailing="static",
        daily_loss=None,
        max_days=None,
    )
    outcome_static, days_static = run_phase(r, rules_static)
    assert outcome_static == "timeout"
    assert days_static == 2

    rules_trailing = PhaseRules(
        profit_target=0.50,
        max_loss=0.04,
        trailing="eod_trailing",
        daily_loss=None,
        max_days=None,
    )
    outcome_trailing, days_trailing = run_phase(r, rules_trailing)
    assert outcome_trailing == "breach"
    assert days_trailing == 2

    # Peak passes +4%: peak_eod becomes 1.06 (> 1.04).
    # Limit trails to min(1.06 - 0.04, 1.0) = 1.0.
    # If equity drops but stays above 1.0, no breach occurs (limit stopped at 1.0).
    r_capped = np.array([0.06, -0.04])  # Day 2 equity: 1.06 * 0.96 = 1.0176 > 1.0
    outcome_capped, _ = run_phase(r_capped, rules_trailing)
    assert outcome_capped == "timeout"

    # If equity then drops below 1.0, it breaches at the capped 1.0 limit.
    r_breach_at_cap = np.array([0.06, -0.04, -0.03])  # Day 3 equity: 1.0176 * 0.97 = 0.987 <= 1.0
    outcome_breach_at_cap, days_breached = run_phase(r_breach_at_cap, rules_trailing)
    assert outcome_breach_at_cap == "breach"
    assert days_breached == 3


def test_consistency():
    """4. Consistency: one +10% day passes without consistency rule.

    With consistency=0.5 it does not pass on that day; passes later once
    enough smaller profitable days make best day <= 50% of total.
    """
    r_single = np.array([0.10])

    rules_no_consistency = PhaseRules(
        profit_target=0.10,
        max_loss=0.50,
        trailing="static",
        daily_loss=None,
        max_days=None,
        consistency=None,
    )
    outcome_no_c, days_no_c = run_phase(r_single, rules_no_consistency)
    assert outcome_no_c == "pass"
    assert days_no_c == 1

    rules_with_consistency = PhaseRules(
        profit_target=0.10,
        max_loss=0.50,
        trailing="static",
        daily_loss=None,
        max_days=None,
        consistency=0.5,
    )
    outcome_c, days_c = run_phase(r_single, rules_with_consistency)
    assert outcome_c == "timeout"
    assert days_c == 1

    # With subsequent smaller profitable days, best day (0.10) becomes <= 50% of total profit
    # Day 1: +10% -> equity = 1.10, profit = 0.10
    # Day 2: +4%  -> equity = 1.144, profit = 0.144
    # Day 3: +4%  -> equity = 1.18976, profit = 0.18976
    # Day 4: +3%  -> equity = 1.22545, profit = 0.22545. Best day 0.10 <= 0.5 * 0.22545 = 0.1127.
    r_extended = np.array([0.10, 0.04, 0.04, 0.03])
    outcome_ext, days_ext = run_phase(r_extended, rules_with_consistency)
    assert outcome_ext == "pass"
    assert days_ext == 4


def test_lever_and_ev_accounting():
    """5. Lever and EV accounting:

    - lever scales and subtracts financing;
    - evaluate gives p_timeout==1 and ev_per_attempt == -fee (plus monthly fees where configured)
      for all-zero returns;
    - block_bootstrap with seeded rng is reproducible and has shape (n_paths, horizon).
    """
    # 5a: lever scaling and financing subtraction
    r = np.array([0.01, -0.02, 0.005])
    leverage = 3.0
    rf_daily = 0.0001
    financing_markup = 0.02
    levered = lever(r, leverage, rf_daily, financing_markup)
    expected = 3.0 * r - 3.0 * 0.0001 - (3.0 * 0.02 / 252.0)
    np.testing.assert_allclose(levered, expected)

    # 5b: evaluate for all-zero returns
    # Preset without monthly fee (ftmo_2step_100k, fee = 540)
    paths_zero = np.zeros((10, 50))
    stats_ftmo = evaluate(paths_zero, PRESETS["ftmo_2step_100k"])
    assert stats_ftmo.p_timeout == 1.0
    assert stats_ftmo.p_pass == 0.0
    assert stats_ftmo.p_breach == 0.0
    assert stats_ftmo.ev_per_attempt == -PRESETS["ftmo_2step_100k"].fee

    # Preset with monthly fee (topstep_50k, fee = 0, monthly_fee = 49)
    # 50 days = ceil(50 / 21) = 3 months -> 3 * 49 = 147
    # ev_per_attempt = -0 - 147 = -147.0
    stats_topstep = evaluate(paths_zero, PRESETS["topstep_50k"])
    assert stats_topstep.p_timeout == 1.0
    assert stats_topstep.p_pass == 0.0
    assert stats_topstep.p_breach == 0.0
    expected_ev_topstep = -PRESETS["topstep_50k"].fee - 3 * PRESETS["topstep_50k"].monthly_fee
    assert stats_topstep.ev_per_attempt == expected_ev_topstep

    # 5c: block_bootstrap reproducibility and shape
    rng1 = np.random.default_rng(42)
    rng2 = np.random.default_rng(42)
    returns_sample = np.linspace(-0.02, 0.02, 100)
    boot1 = block_bootstrap(returns_sample, n_paths=20, horizon=30, block=5, rng=rng1)
    boot2 = block_bootstrap(returns_sample, n_paths=20, horizon=30, block=5, rng=rng2)

    assert boot1.shape == (20, 30)
    np.testing.assert_array_equal(boot1, boot2)


def test_funded_payout_cap_and_activation_fee():
    """A payout request is capped; the rest of the profit stays in the account; activation is charged on pass."""
    from dataclasses import replace

    base = PRESETS["topstep_50k"]
    preset = replace(base, payout_every_days=1, max_payout_request=1_000.0, profit_split=0.9)
    # +4% on day 1 = $2,000 profit on a $50k account. Request capped at $1,000; trader gets 90% of it.
    payout, days, _ = run_funded(np.array([0.04]), preset)
    assert days == 1
    assert payout == pytest.approx(900.0)

    # A path that passes the combine on day 2 (needs 2 min days, best day < 55% of the 6% target) pays the
    # activation fee in EV: all-pass paths give ev = -monthly - activation (no funded days left).
    passing = np.array([[0.03, 0.03]])
    stats = evaluate(passing, preset)
    assert stats.p_pass == 1.0
    assert stats.ev_per_attempt == pytest.approx(-preset.fee - preset.monthly_fee - preset.activation_fee)
