"""
Tests for volatility targeting.

The point of the module is that a quiet leg and a violent leg should not get the
same weight. The tests that matter are therefore the ordering one (a lower-vol
asset gets more) and the two caps, which do different jobs and both bind in
practice: max_leg stops one unusually calm asset owning the book, max_gross is
the long-only budget this framework cannot exceed.
"""

import math

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils.vol_target import MIN_VOL, inverse_vol_weights, periods_per_year, realized_vol


def _series(returns):
    """A close series with the given per-bar returns."""
    return pd.Series(100.0 * np.cumprod(1.0 + np.asarray(returns, dtype=float)))


# ------------------------------------------------------------------
# realized_vol
# ------------------------------------------------------------------


def test_realized_vol_annualizes():
    """A constant-magnitude alternating return has a known daily sigma."""
    daily = [0.01, -0.01] * 60
    vol = realized_vol(_series(daily), window=60, periods_per_year=252.0)
    # std of the alternating series is ~0.01 (slightly above, since the
    # compounded up/down legs are not exactly symmetric).
    assert vol == pytest.approx(0.01 * math.sqrt(252.0), rel=0.05)


def test_realized_vol_scales_with_the_annualization_factor():
    """A bot on the wrong interval must not silently target the wrong risk."""
    daily = [0.01, -0.01] * 60
    slow = realized_vol(_series(daily), periods_per_year=252.0)
    fast = realized_vol(_series(daily), periods_per_year=252.0 * 390)
    assert fast > slow


def test_realized_vol_uses_only_the_window():
    """A calm recent regime must not be dragged up by an old violent one."""
    noisy = [0.10, -0.10] * 40
    calm = [0.001, -0.001] * 40
    vol = realized_vol(_series([*noisy, *calm]), window=30)
    assert vol < realized_vol(_series(noisy), window=30)


@pytest.mark.parametrize("closes", [None, pd.Series(dtype=float), pd.Series([100.0])])
def test_realized_vol_returns_zero_for_unusable_input(closes):
    """0.0 means 'no estimate'; inverse_vol_weights must read it as such."""
    assert realized_vol(closes) == 0.0


def test_realized_vol_of_a_flat_series_is_zero():
    assert realized_vol(pd.Series([100.0] * 80)) == 0.0


def test_periods_per_year_matches_the_backtest_mapping():
    """Single source of truth — a divergence here mis-scales every vol target."""
    from tradingbot.utils.backtest import _get_periods_per_year

    for interval in ("1d", "1h", "1m", "1wk", "1mo"):
        assert periods_per_year(interval) == _get_periods_per_year(interval)


# ------------------------------------------------------------------
# inverse_vol_weights
# ------------------------------------------------------------------


def test_lower_vol_gets_more_weight():
    """The entire reason the module exists."""
    weights = inverse_vol_weights({"CALM": 0.05, "WILD": 0.40}, target_vol=0.02, max_leg=1.0)
    assert weights["CALM"] > weights["WILD"]
    assert weights["CALM"] / weights["WILD"] == pytest.approx(0.40 / 0.05)


def test_weight_hits_the_target_vol():
    weights = inverse_vol_weights({"A": 0.20}, target_vol=0.10, max_leg=1.0)
    assert weights["A"] == pytest.approx(0.5)


def test_max_leg_caps_a_single_quiet_asset():
    """Without this cap one calm asset takes the whole book on one vol estimate."""
    weights = inverse_vol_weights({"CALM": 0.01, "WILD": 0.40}, target_vol=0.10, max_leg=0.15)
    assert weights["CALM"] == pytest.approx(0.15)


def test_max_gross_scales_the_book_down_preserving_ratios():
    """
    Long-only means the budget binds; relative sizing is what must survive.

    target_vol is chosen so the raw weights (0.8 and 0.4) clear max_leg but sum
    to 1.2 — otherwise the leg cap flattens them to equal weights first and the
    ratio this asserts is destroyed before max_gross is ever reached.
    """
    weights = inverse_vol_weights({"A": 0.05, "B": 0.10}, target_vol=0.04, max_leg=1.0, max_gross=1.0)
    assert sum(weights.values()) == pytest.approx(1.0)
    assert weights["A"] / weights["B"] == pytest.approx(2.0)


def test_gross_below_the_cap_is_left_alone():
    """Under-risked is a real state, not something to lever back up to 1.0."""
    weights = inverse_vol_weights({"A": 0.40, "B": 0.40}, target_vol=0.05, max_leg=1.0)
    assert sum(weights.values()) == pytest.approx(0.25)


@pytest.mark.parametrize("bad", [0.0, -0.1, float("nan"), float("inf"), None, "x"])
def test_unusable_vol_drops_the_leg(bad):
    """Sizing against a guess is how a data outage becomes a position."""
    weights = inverse_vol_weights({"BAD": bad, "GOOD": 0.20}, target_vol=0.10, max_leg=1.0)
    assert "BAD" not in weights
    assert weights["GOOD"] == pytest.approx(0.5)


def test_vol_floor_prevents_an_infinite_weight():
    tiny = inverse_vol_weights({"A": MIN_VOL / 1000}, target_vol=0.10, max_leg=1.0)
    assert tiny["A"] <= 1.0


def test_empty_input_returns_empty():
    assert inverse_vol_weights({}, target_vol=0.10) == {}


def test_never_returns_a_usd_key():
    """Cash is the caller's residual — a USD key here would double-count it."""
    assert "USD" not in inverse_vol_weights({"A": 0.2}, target_vol=0.1)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"target_vol": 0.0}, "target_vol"),
        ({"target_vol": 0.1, "max_leg": 0.0}, "max_leg"),
        ({"target_vol": 0.1, "max_gross": 1.5}, "max_gross"),
    ],
)
def test_invalid_parameters_raise(kwargs, match):
    with pytest.raises(ValueError, match=match):
        inverse_vol_weights({"A": 0.2}, **kwargs)
