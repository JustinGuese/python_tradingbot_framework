"""
Portfolio weight normalization — one implementation of one invariant.

This existed inline in five places with four different tolerances for "does this
sum to 1.0": 0.001 in aihedgefundbot and aideepseektoolbot's rebalance path, 0.01
in aideepseektoolbot's LLM tool and portfolio_manager's guard, and no check at all
in portfolio_utils. Those tolerances were also doing two different jobs — deciding
whether to *normalize*, and deciding whether to *reject* — which is why they drifted.

The split here separates those jobs:

- `normalize_weights` is unconditional. Scaling weights that already sum to 1.0 is
  a no-op, so gating it behind a tolerance only created a band in which weights
  were left un-normalized: at the old 0.001 gate, a set summing to 1.005 passed
  through unchanged and over-allocated by half a percent. Now the output always
  sums to exactly 1.0 (up to float precision).
- `require_normalized` / `is_normalized` are for validating input someone else
  produced — an LLM tool call, or a caller-supplied target portfolio — where the
  right response is to reject rather than silently rescale.

Deliberately dependency-free (no pypfopt, no pandas) so it stays cheap to import
from anywhere, including the live-trading path.
"""

import logging

logger = logging.getLogger(__name__)

#: Single tolerance for "these weights sum to 1.0". Used only for validation and
#: warnings — never to decide whether normalization runs.
WEIGHT_SUM_TOLERANCE = 0.01


def positive_weight_sum(weights: dict[str, float]) -> float:
    """Sum of the positive weights only.

    Negative and zero entries are ignored because every caller treats a
    non-positive weight as "do not hold", not as a short.
    """
    return sum(w for w in weights.values() if w > 0)


def is_normalized(weights: dict[str, float], tolerance: float = WEIGHT_SUM_TOLERANCE) -> bool:
    """True if the positive weights sum to 1.0 within `tolerance`."""
    return abs(positive_weight_sum(weights) - 1.0) <= tolerance


def normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    """Scale positive weights so they sum to exactly 1.0.

    Non-positive weights are clamped to 0.0 rather than dropped, so the caller
    keeps the full symbol set and can tell "explicitly zero" from "absent" — which
    matters to rebalancePortfolio, where a symbol at 0.0 means *sell it* and a
    missing symbol means *leave it alone*.

    Returns an empty dict when no positive weight exists; there is no meaningful
    normalization of an all-zero portfolio, and callers must handle that case.
    """
    total = positive_weight_sum(weights)
    if total <= 0:
        return {}
    return {k: (v / total if v > 0 else 0.0) for k, v in weights.items()}


def require_normalized(
    weights: dict[str, float],
    tolerance: float = WEIGHT_SUM_TOLERANCE,
    context: str = "Target portfolio weights",
) -> None:
    """Raise ValueError unless `weights` sums to 1.0 within `tolerance`.

    For input that a caller supplied and must get right, as opposed to weights
    this code derived and may freely rescale.
    """
    total = sum(weights.values())
    if abs(total - 1.0) > tolerance:
        raise ValueError(f"{context} must sum to 1.0, got {total}")
