"""
Volatility targeting — size each leg by its own risk, not by counting legs.

Equal weighting says a bond leg and a commodity leg are the same size. They are
not: over the last two decades IEF has run near 6% annualized vol and USO near
40%, so an equal-weight book is really a commodity bet with some bonds attached.
Scaling each leg to a common ex-ante volatility is what makes a multi-sector
book behave like the diversified thing it looks like on paper, and it is the
sizing rule in Moskowitz/Ooi/Pedersen (2012).

This logic existed only inside SynthesizedHyperConvexityBot._kelly_fraction.
Three bots now need it, so it lives here.

**The long-only caveat, stated once and inherited by every caller.** The papers
lever each leg up to the target and let gross exposure exceed 1. This book
cannot: weights are non-negative and sum to at most 1.0 (see weights.py). So
`max_gross` is a real constraint, not a formality — in calm markets the
unconstrained weights sum to well under 1 and the residual sits in cash
(under-risked versus the paper), while in stressed markets they sum to more than
1 and get scaled back proportionally. The result is a strategy with the paper's
*relative* sizing but a lower and time-varying absolute risk level. Any backtest
of a caller must be described that way rather than as a replication.

Deliberately dependency-light — pandas plus the stdlib, like weights.py — so it
stays cheap to import from the live-trading path.
"""

import logging
import math

import pandas as pd

logger = logging.getLogger(__name__)

#: Vol floor used when scaling. A leg whose measured volatility rounds to zero
#: (a stale or constant price series) would otherwise divide into an infinite
#: weight and take the whole book. 0.5% annualized is below anything genuinely
#: tradeable, so this only ever fires on bad data.
MIN_VOL = 0.005


def periods_per_year(interval: str) -> float:
    """
    Bars per trading year for `interval` — the annualization factor for realized_vol.

    Delegates to backtest.py's mapping rather than restating it, so a bot's vol
    target cannot mean one thing here and another in its own backtest. Imported
    lazily to keep this module's import cost off the live-trading path.
    """
    from .backtest import _get_periods_per_year

    return _get_periods_per_year(interval)


def realized_vol(closes: pd.Series, window: int = 60, periods_per_year: float = 252.0) -> float:
    """
    Annualized realized volatility from a close series.

    Uses simple percentage returns rather than log returns: the weights derived
    from this are compared against a percentage vol target, and over a 60-bar
    window the two differ by far less than the estimation error either carries.

    Args:
        closes: Close prices, oldest first. Only the last `window` returns are used.
        window: Number of returns in the estimate.
        periods_per_year: Annualization factor — use _get_periods_per_year(interval)
            from backtest.py rather than hardcoding, or a bot on a non-daily
            interval will silently target the wrong risk level.

    Returns:
        Annualized volatility as a fraction (0.18 == 18%), or 0.0 if there is
        not enough data. A 0.0 return means "no estimate", and callers must not
        read it as "no risk" — inverse_vol_weights drops such legs.
    """
    if closes is None or len(closes) < 3:
        return 0.0
    returns = pd.Series(closes).astype(float).pct_change().dropna()
    if len(returns) < 2:
        return 0.0
    sample = returns.iloc[-window:]
    if len(sample) < 2:
        return 0.0
    sigma = float(sample.std())
    if not math.isfinite(sigma) or sigma <= 0:
        return 0.0
    return sigma * math.sqrt(periods_per_year)


def inverse_vol_weights(
    vols: dict[str, float],
    target_vol: float,
    max_leg: float = 0.20,
    max_gross: float = 1.0,
) -> dict[str, float]:
    """
    Weight each leg so it contributes `target_vol` of annualized risk.

    Each leg gets `target_vol / vol_i`, capped at `max_leg`. If the resulting
    gross exceeds `max_gross` the whole book is scaled down proportionally,
    which preserves the *relative* sizing the vol targeting produced — the thing
    actually worth keeping — and puts the shortfall in cash.

    Note the two caps do different jobs and both are needed. `max_leg` is a
    concentration limit: it stops a single unusually quiet asset from taking
    most of the book on the strength of one vol estimate. `max_gross` is the
    long-only budget constraint. Applying only the second would let a calm
    market produce a one-legged portfolio.

    Args:
        vols: Annualized volatility per symbol, as returned by realized_vol().
            A non-positive or non-finite entry means "no estimate" and the leg
            is dropped — sizing it against a guess is how a data outage becomes
            a position.
        target_vol: Annualized volatility each leg should contribute (0.10 == 10%).
        max_leg: Hard per-leg weight cap.
        max_gross: Maximum sum of weights. Must be <= 1.0 for this framework.

    Returns:
        Weights per symbol, non-negative, summing to at most `max_gross`.
        Empty if no leg has a usable volatility estimate. Cash is the caller's
        residual — this function never returns a "USD" key.
    """
    if target_vol <= 0:
        raise ValueError(f"target_vol must be positive, got {target_vol}")
    if not 0 < max_leg <= 1.0:
        raise ValueError(f"max_leg must be in (0, 1], got {max_leg}")
    if not 0 < max_gross <= 1.0:
        raise ValueError(f"max_gross must be in (0, 1], got {max_gross}")

    weights: dict[str, float] = {}
    for symbol, vol in (vols or {}).items():
        try:
            sigma = float(vol)
        except (TypeError, ValueError):
            sigma = 0.0
        if not math.isfinite(sigma) or sigma <= 0:
            logger.debug("inverse_vol_weights: no usable vol for %s (%r) — dropping", symbol, vol)
            continue
        weights[symbol] = min(target_vol / max(sigma, MIN_VOL), max_leg)

    gross = sum(weights.values())
    if gross > max_gross and gross > 0:
        scale = max_gross / gross
        weights = {s: w * scale for s, w in weights.items()}

    return weights
