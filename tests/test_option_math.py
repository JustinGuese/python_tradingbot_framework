"""
option_math: every formula checked against a textbook value, a no-arbitrage
identity, or a finite difference of bs_price itself.
"""

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils import option_math as om

# Hull, Options Futures & Other Derivatives, Example 15.6.
S, K, T, R, SIG = 42.0, 40.0, 0.5, 0.10, 0.20


def test_black_scholes_textbook_values():
    assert om.bs_price(S, K, T, R, SIG, "C") == pytest.approx(4.76, abs=0.005)
    assert om.bs_price(S, K, T, R, SIG, "P") == pytest.approx(0.81, abs=0.005)


@pytest.mark.parametrize("q", [0.0, 0.02])
def test_put_call_parity(q):
    call = om.bs_price(S, K, T, R, SIG, "call", q)
    put = om.bs_price(S, K, T, R, SIG, "put", q)
    assert call - put == pytest.approx(S * math.exp(-q * T) - K * math.exp(-R * T))


def test_expired_option_is_worth_intrinsic():
    assert om.bs_price(42, 40, 0, R, SIG, "C") == 2.0
    assert om.bs_price(42, 40, 0, R, SIG, "P") == 0.0
    assert om.delta(42, 40, 0, R, SIG, "C") == 1.0


@pytest.mark.parametrize("right", ["C", "P"])
@pytest.mark.parametrize("q", [0.0, 0.03])
def test_greeks_match_finite_differences(right, q):
    g = om.greeks(S, K, T, R, SIG, right, q)
    h = 1e-3

    def p(s=S, t=T, r=R, sig=SIG):
        return om.bs_price(s, K, t, r, sig, right, q)

    assert g.delta == pytest.approx((p(s=S + h) - p(s=S - h)) / (2 * h), abs=1e-5)
    assert g.gamma == pytest.approx((p(s=S + h) - 2 * p() + p(s=S - h)) / h**2, abs=1e-4)
    assert g.vega == pytest.approx((p(sig=SIG + 0.01) - p(sig=SIG - 0.01)) / 2, abs=1e-4)
    assert g.rho == pytest.approx((p(r=R + 0.01) - p(r=R - 0.01)) / 2, abs=1e-4)
    one_day = 1 / om.DAYS_PER_YEAR
    assert g.theta == pytest.approx(p(t=T - one_day) - p(), abs=1e-3)


def test_delta_ranges_and_long_option_decays():
    assert 0 < om.delta(S, K, T, R, SIG, "C") < 1
    assert -1 < om.delta(S, K, T, R, SIG, "P") < 0
    assert om.theta(S, K, T, R, SIG, "C") < 0


@pytest.mark.parametrize("right", ["C", "P"])
@pytest.mark.parametrize("sigma", [0.08, 0.25, 0.9])
def test_implied_volatility_round_trip(right, sigma):
    price = om.bs_price(S, K, T, R, sigma, right)
    assert om.implied_volatility(price, S, K, T, R, right) == pytest.approx(sigma, abs=1e-4)


def test_implied_volatility_none_outside_no_arbitrage_band():
    # Below discounted intrinsic (e.g. a stale print): no sigma produces it.
    assert om.implied_volatility(1.0, 50, 40, 0.5, R, "C") is None
    assert om.implied_volatility(0.0, S, K, T, R, "C") is None
    assert om.implied_volatility(60.0, S, K, T, R, "C") is None  # above S


@pytest.mark.parametrize("right,target", [("C", 0.70), ("C", 0.16), ("P", 0.30), ("P", 0.16)])
def test_strike_for_delta_inverts_delta(right, target):
    k = om.strike_for_delta(300, 1.2, 0.04, 0.25, target, right)
    assert abs(om.delta(300, k, 1.2, 0.04, 0.25, right)) == pytest.approx(target, abs=1e-6)


def test_course_breakeven_example():
    """Course notes: $195 call bought at $20.60 breaks even at $215.60."""
    assert om.breakeven(195, 20.6, "call") == pytest.approx(215.6)
    assert om.breakeven(195, 20.6, "put") == pytest.approx(174.4)


def test_course_beta_exposure_example():
    """$60 TSLA long at beta 1.89 + $40 KO short at beta 0.6 -> 0.89 beta exposure."""
    book = [(60.0, 1.89), (-40.0, 0.6)]
    assert om.beta_weighted_dollars(book) == pytest.approx(89.4)
    assert om.beta_exposure(book) == pytest.approx(0.894)


def test_beta_of_a_levered_series():
    bench = pd.Series(np.random.default_rng(0).normal(0, 0.01, 500))
    assert om.beta(2 * bench, bench) == pytest.approx(2.0)


def test_historical_volatility_of_constant_daily_move():
    # Alternating +1%/-1% log moves: std ~0.01 per day -> ~15.9% annualised.
    log_moves = np.tile([0.01, -0.01], 50)
    close = pd.Series(100 * np.exp(np.cumsum(log_moves)))
    assert om.historical_volatility(close, window=20) == pytest.approx(0.01 * math.sqrt(252), rel=0.03)


def test_iv_rank_percentile_and_ratio():
    history = [0.20, 0.25, 0.30, 0.40]
    assert om.iv_rank(history, 0.30) == pytest.approx(50.0)
    assert om.iv_percentile(history, 0.30) == pytest.approx(50.0)
    assert om.iv_hv_ratio(0.33, 0.30) == pytest.approx(1.1)
    assert om.iv_hv_ratio(0.33, 0.0) is None


def test_expected_move_and_probability_itm():
    assert om.expected_move(100, 0.365, 365) == pytest.approx(36.5)
    assert om.probability_itm(100, 100, 1.0, 0.0, 0.2, "C") == pytest.approx(om.norm_cdf(-0.1))
    p_c = om.probability_itm(100, 110, 0.5, 0.03, 0.3, "C")
    assert p_c + om.probability_itm(100, 110, 0.5, 0.03, 0.3, "P") == pytest.approx(1.0)


def test_year_fraction():
    assert om.year_fraction(date(2027, 9, 25), date(2026, 9, 25)) == pytest.approx(1.0)
    assert om.year_fraction(date(2026, 9, 1), date(2026, 9, 25)) == 0.0


# ------------------------------------------------------------------
# Multi-leg payoffs
# ------------------------------------------------------------------

BULL_PUT = [om.Leg("P", 100, -1, premium=3.0), om.Leg("P", 90, 1, premium=1.0)]  # $2 credit, $10 wide
IRON_CONDOR = [
    om.Leg("P", 90, 1, 0.5),
    om.Leg("P", 100, -1, 2.0),
    om.Leg("C", 120, -1, 2.0),
    om.Leg("C", 130, 1, 0.5),
]  # $3 credit, $10 wings


def test_bull_put_spread_payoff():
    assert om.max_profit(BULL_PUT) == pytest.approx(2.0)
    assert om.max_loss(BULL_PUT) == pytest.approx(8.0)  # width - credit
    assert om.breakevens(BULL_PUT) == [pytest.approx(98.0)]
    assert om.payoff_at_expiry(BULL_PUT, 150) == pytest.approx(2.0)
    assert om.payoff_at_expiry(BULL_PUT, 50) == pytest.approx(-8.0)


def test_iron_condor_payoff():
    assert om.max_profit(IRON_CONDOR) == pytest.approx(3.0)
    assert om.max_loss(IRON_CONDOR) == pytest.approx(7.0)
    assert om.breakevens(IRON_CONDOR) == [pytest.approx(97.0), pytest.approx(123.0)]


def test_unbounded_structures():
    naked_call = [om.Leg("C", 100, -1, 2.0)]
    assert om.max_loss(naked_call) == math.inf
    long_call = [om.Leg("C", 100, 1, 2.0)]
    assert om.max_profit(long_call) == math.inf
    assert om.max_loss(long_call) == pytest.approx(2.0)
    assert om.breakevens(long_call) == [pytest.approx(102.0)]


def test_cash_secured_put_max_loss_is_strike_minus_credit():
    assert om.max_loss([om.Leg("P", 100, -1, 3.0)]) == pytest.approx(97.0)


def test_probability_of_profit():
    # A long call is profitable exactly when S_T > breakeven.
    long_call = [om.Leg("C", 100, 1, 5.0)]
    expected = om.probability_itm(100, 105, 0.5, 0.03, 0.25, "C")
    assert om.probability_of_profit(long_call, 100, 0.5, 0.03, 0.25) == pytest.approx(expected)
    # A condor wins between its breakevens: its PoP plus both tails is 1.
    pop = om.probability_of_profit(IRON_CONDOR, 110, 0.1, 0.0, 0.2)
    below = om.probability_itm(110, 97, 0.1, 0.0, 0.2, "P")
    above = om.probability_itm(110, 123, 0.1, 0.0, 0.2, "C")
    assert pop + below + above == pytest.approx(1.0)


def test_greeks_scale_and_add():
    g = om.greeks(S, K, T, R, SIG, "C")
    assert (g.scaled(-100) + g.scaled(100)).delta == pytest.approx(0.0)
    assert g.scaled(100).delta == pytest.approx(100 * g.delta)


def test_liquidity_helpers():
    assert om.option_dollar_volume(2.5, 1000) == 250_000
    assert om.spread_pct(1.0, 1.1) == pytest.approx(0.1 / 1.05)
    assert om.spread_pct(0.0, 1.1) == math.inf
    assert om.delta_dollars(0.7, 300, 400) == pytest.approx(84_000)
