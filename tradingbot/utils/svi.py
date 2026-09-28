"""
Raw SVI (Gatheral 2004): a five-parameter fit of the volatility smile's total
variance, plus the no-arbitrage checks from Gatheral & Jacquier (2014).

Pure functions only: no database, no network, no ChainView/pandas — plain
numpy arrays and sequences, so callers (options.py, the mispricing bot,
one-off notebooks) adapt whatever shape they have.

Conventions:
- `k` is log-moneyness, ln(K/F), F the forward.
- `w` is total variance, `iv**2 * tau`, tau in years.
- `w(k) = a + b*(rho*(k-m) + sqrt((k-m)**2 + sigma**2))` is the raw SVI curve
  for one expiry. Fitting is per expiry; comparing fits across expiries
  (`calendar_arbitrage_free`) is how the term structure is checked.

Arbitrage:
- Static (butterfly) freedom needs `w > 0` and Gatheral's density function
  `g(k) >= 0` everywhere — `g < 0` somewhere means the fitted smile implies a
  negative risk-neutral density, i.e. a butterfly with negative price.
- Calendar freedom needs total variance non-decreasing in tau at every k —
  a later expiry's smile must sit at or above an earlier one's, or a
  calendar spread would arbitrage the pair.

These are checks on the FITTED curve, not the raw quotes: a smile can fit
well in a least-squares sense and still admit arbitrage, which is exactly
what `butterfly_arbitrage_free` / `calendar_arbitrage_free` are for.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.optimize import least_squares

_DEFAULT_K_GRID = np.linspace(-1.5, 1.5, 301)


@dataclass(frozen=True)
class SVIParams:
    a: float
    b: float
    rho: float
    m: float
    sigma: float

    def w(self, k: Sequence[float] | np.ndarray | float) -> np.ndarray:
        """Total variance at log-moneyness k (vectorised)."""
        k_arr = np.asarray(k, dtype=float)
        dk = k_arr - self.m
        return self.a + self.b * (self.rho * dk + np.sqrt(dk * dk + self.sigma * self.sigma))

    def w_prime(self, k: Sequence[float] | np.ndarray | float) -> np.ndarray:
        """dw/dk."""
        k_arr = np.asarray(k, dtype=float)
        dk = k_arr - self.m
        return self.b * (self.rho + dk / np.sqrt(dk * dk + self.sigma * self.sigma))

    def w_second(self, k: Sequence[float] | np.ndarray | float) -> np.ndarray:
        """d2w/dk2."""
        k_arr = np.asarray(k, dtype=float)
        dk = k_arr - self.m
        return self.b * self.sigma * self.sigma / np.power(dk * dk + self.sigma * self.sigma, 1.5)

    def iv(self, k: Sequence[float] | np.ndarray | float, tau: float) -> np.ndarray:
        """Implied vol at log-moneyness k and time-to-expiry tau (years)."""
        if tau <= 0:
            raise ValueError(f"tau must be > 0 (got {tau})")
        return np.sqrt(np.maximum(self.w(k), 0.0) / tau)


def _svi_residuals(x: np.ndarray, k: np.ndarray, w: np.ndarray, weights: np.ndarray | None) -> np.ndarray:
    a, b, rho, m, sigma = x
    dk = k - m
    model = a + b * (rho * dk + np.sqrt(dk * dk + sigma * sigma))
    res = model - w
    if weights is not None:
        res = res * weights
    # Gatheral's minimum-total-variance condition, a + b*sigma*sqrt(1-rho^2) >= 0.
    # Enforced as a penalty term rather than a hard constraint, since it mixes
    # all five parameters and least_squares only bounds them individually.
    min_w = a + b * sigma * math.sqrt(max(1.0 - rho * rho, 0.0))
    penalty = max(0.0, -min_w) * 1.0e4
    return np.append(res, penalty)


def fit_svi(
    k: Sequence[float] | np.ndarray,
    w: Sequence[float] | np.ndarray,
    weights: Sequence[float] | np.ndarray | None = None,
) -> SVIParams | None:
    """
    Fit raw SVI total variance to (k, w) points by nonlinear least squares.

    `weights` multiply the residuals (e.g. inverse half-spread-in-vol, so
    tight-quoted strikes pull the fit harder). Needs at least 5 points, since
    5 parameters are being fit; fewer, or an optimiser failure, return None.
    """
    k_arr = np.asarray(k, dtype=float)
    w_arr = np.asarray(w, dtype=float)
    if k_arr.shape != w_arr.shape:
        raise ValueError("k and w must have the same shape")
    if len(k_arr) < 5:
        return None

    weights_arr: np.ndarray | None = None
    if weights is not None:
        weights_arr = np.asarray(weights, dtype=float)
        if weights_arr.shape != k_arr.shape:
            raise ValueError("weights must have the same shape as k/w")

    k_range = float(np.max(k_arr) - np.min(k_arr))
    w_range = float(np.max(w_arr) - np.min(w_arr))
    idx_min = int(np.argmin(w_arr))
    a0 = float(w_arr[idx_min])
    m0 = float(k_arr[idx_min])
    sigma0 = max(1.0e-3, 0.1 * k_range) if k_range > 0 else 0.1
    b0 = max(1.0e-3, w_range / (k_range + 1.0e-6)) if k_range > 0 else 1.0e-3
    x0 = np.array([a0, b0, 0.0, m0, sigma0], dtype=float)

    lb = np.array([-np.inf, 1.0e-8, -0.999, -np.inf, 1.0e-4])
    ub = np.array([np.inf, np.inf, 0.999, np.inf, np.inf])

    try:
        result = least_squares(_svi_residuals, x0, bounds=(lb, ub), args=(k_arr, w_arr, weights_arr))
    except Exception:
        return None
    if not result.success:
        return None

    a, b, rho, m, sigma = (float(v) for v in result.x)
    return SVIParams(a=a, b=b, rho=rho, m=m, sigma=sigma)


def butterfly_density(params: SVIParams, k: Sequence[float] | np.ndarray | float) -> np.ndarray:
    """
    Gatheral's g(k): risk-neutral density is proportional to g(k) / sqrt(w).
    g(k) < 0 somewhere means the smile prices a butterfly negatively.
    """
    k_arr = np.asarray(k, dtype=float)
    w = params.w(k_arr)
    wp = params.w_prime(k_arr)
    wpp = params.w_second(k_arr)
    with np.errstate(divide="ignore", invalid="ignore"):
        term1 = np.power(1.0 - k_arr * wp / (2.0 * w), 2.0)
        term2 = (wp * wp / 4.0) * (1.0 / w + 0.25)
        g = term1 - term2 + wpp / 2.0
    return g


def butterfly_arbitrage_free(
    params: SVIParams, k_grid: Sequence[float] | np.ndarray | None = None, tol: float = 1.0e-8
) -> bool:
    """True iff w > 0 and g(k) >= -tol everywhere on k_grid (default a wide, dense grid)."""
    grid = _DEFAULT_K_GRID if k_grid is None else np.asarray(k_grid, dtype=float)
    w = params.w(grid)
    if np.any(w <= 0):
        return False
    g = butterfly_density(params, grid)
    if np.any(~np.isfinite(g)):
        return False
    return bool(np.all(g >= -tol))


def calendar_arbitrage_free(
    fits: Sequence[tuple[float, SVIParams]],
    k_grid: Sequence[float] | np.ndarray | None = None,
    tol: float = 1.0e-6,
) -> bool:
    """
    True iff, sorted by tau, total variance is non-decreasing in tau at every
    k on the grid: w_{i+1}(k) >= w_i(k) - tol for every k, every consecutive
    pair of expiries.
    """
    if len(fits) < 2:
        return True
    grid = _DEFAULT_K_GRID if k_grid is None else np.asarray(k_grid, dtype=float)
    ordered = sorted(fits, key=lambda item: item[0])
    prev_w = ordered[0][1].w(grid)
    for _tau, params in ordered[1:]:
        w = params.w(grid)
        if np.any(w < prev_w - tol):
            return False
        prev_w = w
    return True


@dataclass(frozen=True)
class SVIOutlier:
    strike: float
    right: str
    iv: float
    fit_iv: float
    resid: float
    half_spread_vol: float


def svi_outliers(
    strikes: Sequence[float],
    rights: Sequence[str],
    ivs: Sequence[float],
    half_spread_vols: Sequence[float],
    forward: float,
    tau: float,
    params: SVIParams,
) -> list[SVIOutlier]:
    """
    Contracts whose quoted iv sits further from the fitted SVI curve than
    their own half bid/ask spread (in vol terms) — i.e. mispriced beyond what
    the spread can explain. Sorted by |resid| descending. NaN inputs are
    skipped rather than raising, since real chains have stale/missing quotes.
    """
    if forward <= 0 or tau <= 0:
        raise ValueError(f"forward and tau must be > 0 (got forward={forward}, tau={tau})")

    out: list[SVIOutlier] = []
    for strike, right, iv_val, half_spread in zip(strikes, rights, ivs, half_spread_vols, strict=True):
        if strike is None or strike <= 0:
            continue
        if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in (iv_val, half_spread)):
            continue
        k = math.log(strike / forward)
        fit_iv = float(params.iv(k, tau))
        if math.isnan(fit_iv):
            continue
        resid = float(iv_val) - fit_iv
        if abs(resid) > half_spread:
            out.append(
                SVIOutlier(
                    strike=float(strike),
                    right=right,
                    iv=float(iv_val),
                    fit_iv=fit_iv,
                    resid=resid,
                    half_spread_vol=float(half_spread),
                )
            )
    out.sort(key=lambda o: abs(o.resid), reverse=True)
    return out


def half_spread_in_vol(bid: float, ask: float, vega_per_point: float) -> float:
    """
    Half the bid/ask spread, converted from price to vol units.

    `vega_per_point` follows option_math.vega's convention: price change per
    1 vol POINT (sigma + 0.01), i.e. per-0.01-vol. To convert a price
    half-spread into vol units we need price-per-1.00-vol, which is
    vega_per_point * 100. NaN on any invalid or missing input (crossed quote,
    non-positive vega, None/NaN).
    """
    values = (bid, ask, vega_per_point)
    if any(v is None for v in values):
        return float("nan")
    if any(math.isnan(v) for v in values):
        return float("nan")
    if bid < 0 or ask < bid or vega_per_point <= 0:
        return float("nan")
    return (ask - bid) / 2.0 / (vega_per_point * 100.0)
