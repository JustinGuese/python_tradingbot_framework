"""
Tests for the shared weight-normalization helpers.

These replaced five inline implementations that used four different tolerances
for one invariant, and that conflated "rescale these" with "reject these".
"""

import pytest

from tradingbot.utils.weights import (
    WEIGHT_SUM_TOLERANCE,
    is_normalized,
    normalize_weights,
    positive_weight_sum,
    require_normalized,
)


def test_normalize_scales_to_exactly_one():
    assert normalize_weights({"A": 1.0, "B": 3.0}) == pytest.approx({"A": 0.25, "B": 0.75})


def test_normalize_is_idempotent():
    already = {"A": 0.6, "B": 0.4}
    assert normalize_weights(already) == pytest.approx(already)


def test_normalize_closes_the_old_tolerance_gap():
    """The old 0.001-gated code left a set summing to 1.005 untouched.

    That over-allocated by half a percent, silently. Normalization is now
    unconditional, so the sum is 1.0 regardless of how close the input was.
    """
    drifted = {"A": 0.605, "B": 0.400}
    assert positive_weight_sum(drifted) == pytest.approx(1.005)
    assert positive_weight_sum(normalize_weights(drifted)) == pytest.approx(1.0)


def test_non_positive_weights_are_clamped_not_dropped():
    # rebalancePortfolio reads an explicit 0.0 as "sell it" and a missing symbol
    # as "leave it alone", so the symbol set must survive normalization.
    result = normalize_weights({"A": 1.0, "B": 0.0, "C": -0.5})
    assert set(result) == {"A", "B", "C"}
    assert result["A"] == pytest.approx(1.0)
    assert result["B"] == 0.0
    assert result["C"] == 0.0


def test_all_zero_portfolio_normalizes_to_empty():
    assert normalize_weights({"A": 0.0, "B": 0.0}) == {}
    assert normalize_weights({}) == {}


def test_negative_only_portfolio_normalizes_to_empty():
    assert normalize_weights({"A": -1.0}) == {}


def test_positive_weight_sum_ignores_non_positive():
    assert positive_weight_sum({"A": 0.7, "B": -0.2, "C": 0.0}) == pytest.approx(0.7)


@pytest.mark.parametrize("total", [1.0, 1.0 + WEIGHT_SUM_TOLERANCE / 2, 1.0 - WEIGHT_SUM_TOLERANCE / 2])
def test_is_normalized_accepts_within_tolerance(total):
    assert is_normalized({"A": total})


@pytest.mark.parametrize("total", [0.5, 1.5, 0.0])
def test_is_normalized_rejects_outside_tolerance(total):
    assert not is_normalized({"A": total})


def test_require_normalized_passes_on_valid_input():
    require_normalized({"A": 0.8, "B": 0.2})  # must not raise


def test_require_normalized_raises_with_the_actual_total():
    with pytest.raises(ValueError, match=r"1\.5"):
        require_normalized({"A": 1.5})


def test_require_normalized_context_names_the_caller():
    with pytest.raises(ValueError, match="Model weights"):
        require_normalized({"A": 2.0}, context="Model weights")
