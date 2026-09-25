"""
Option math: Black-Scholes-Merton pricing, the greeks, implied volatility, and
the everyday numbers an options trader reaches for.

Pure functions only: no database, no network; numpy/pandas only for the
series helpers. `options.py` and the option bots build on these; they are
also what the synthetic backtest prices with, so live and backtest share one
set of formulas.

Conventions:
- `right` is "C" / "P" (also accepts "call" / "put").
- `T` is in years (use `year_fraction`), `r` and `q` continuously compounded,
  `sigma` annualised (0.25 = 25%).
- Prices are per share. A contract is CONTRACT_MULTIPLIER shares.
- Greeks are per share: theta per CALENDAR day, vega per 1 vol point (0.01),
  rho per 1 rate point (0.01) — the units a broker's option chain shows.

Black-Scholes prices European options. US equity options are American; for a
call on a low-dividend stock like AAPL the early-exercise premium is negligible,
for deep ITM puts it is not. Good enough for selection, sizing and a synthetic
backtest; not a market maker's model.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from statistics import NormalDist

import numpy as np
import pandas as pd

CONTRACT_MULTIPLIER = 100
DAYS_PER_YEAR = 365.0
TRADING_DAYS_PER_YEAR = 252

_N = NormalDist()


def _right(right: str) -> str:
    key = str(right).strip().upper()
    if key in ("C", "CALL"):
        return "C"
    if key in ("P", "PUT"):
        return "P"
    raise ValueError(f"right={right!r}: use 'C' / 'P'")


def norm_cdf(x: float) -> float:
    return _N.cdf(x)


def norm_pdf(x: float) -> float:
    return _N.pdf(x)


def year_fraction(expiry: date, today: date) -> float:
    """Calendar time to expiry in years, floored at 0."""
    return max((expiry - today).days, 0) / DAYS_PER_YEAR


def intrinsic(S: float, K: float, right: str) -> float:
    return max(0.0, S - K) if _right(right) == "C" else max(0.0, K - S)


def d1_d2(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> tuple[float, float]:
    if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
        raise ValueError(f"d1/d2 need S, K, T, sigma > 0 (got S={S}, K={K}, T={T}, sigma={sigma})")
    vol_t = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vol_t
    return d1, d1 - vol_t


def bs_price(S: float, K: float, T: float, r: float, sigma: float, right: str, q: float = 0.0) -> float:
    """Black-Scholes-Merton price of a European option, per share."""
    right = _right(right)
    if T <= 0:
        return intrinsic(S, K, right)
    if sigma <= 0:
        # Deterministic forward: the discounted intrinsic value of the forward.
        fwd = S * math.exp(-q * T) - K * math.exp(-r * T)
        return max(fwd, 0.0) if right == "C" else max(-fwd, 0.0)
    d1, d2 = d1_d2(S, K, T, r, sigma, q)
    disc_s = S * math.exp(-q * T)
    disc_k = K * math.exp(-r * T)
    if right == "C":
        return disc_s * norm_cdf(d1) - disc_k * norm_cdf(d2)
    return disc_k * norm_cdf(-d2) - disc_s * norm_cdf(-d1)


# ------------------------------------------------------------------
# Greeks
# ------------------------------------------------------------------


def delta(S: float, K: float, T: float, r: float, sigma: float, right: str, q: float = 0.0) -> float:
    """dPrice/dS. Calls 0..1, puts -1..0."""
    right = _right(right)
    if T <= 0 or sigma <= 0:
        itm = S > K if right == "C" else S < K
        return (1.0 if right == "C" else -1.0) if itm else 0.0
    d1, _ = d1_d2(S, K, T, r, sigma, q)
    carry = math.exp(-q * T)
    return carry * norm_cdf(d1) if right == "C" else carry * (norm_cdf(d1) - 1.0)


def gamma(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    """d2Price/dS2, identical for calls and puts."""
    if T <= 0 or sigma <= 0:
        return 0.0
    d1, _ = d1_d2(S, K, T, r, sigma, q)
    return math.exp(-q * T) * norm_pdf(d1) / (S * sigma * math.sqrt(T))


def vega(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    """Price change per 1 vol point (sigma + 0.01), identical for calls and puts."""
    if T <= 0 or sigma <= 0:
        return 0.0
    d1, _ = d1_d2(S, K, T, r, sigma, q)
    return S * math.exp(-q * T) * norm_pdf(d1) * math.sqrt(T) / 100.0


def theta(S: float, K: float, T: float, r: float, sigma: float, right: str, q: float = 0.0) -> float:
    """Price change per calendar day that passes (negative for a long option: decay)."""
    right = _right(right)
    if T <= 0 or sigma <= 0:
        return 0.0
    d1, d2 = d1_d2(S, K, T, r, sigma, q)
    disc_s = S * math.exp(-q * T)
    disc_k = K * math.exp(-r * T)
    decay = -disc_s * norm_pdf(d1) * sigma / (2.0 * math.sqrt(T))
    if right == "C":
        per_year = decay - r * disc_k * norm_cdf(d2) + q * disc_s * norm_cdf(d1)
    else:
        per_year = decay + r * disc_k * norm_cdf(-d2) - q * disc_s * norm_cdf(-d1)
    return per_year / DAYS_PER_YEAR


def rho(S: float, K: float, T: float, r: float, sigma: float, right: str, q: float = 0.0) -> float:
    """Price change per 1 rate point (r + 0.01)."""
    right = _right(right)
    if T <= 0 or sigma <= 0:
        return 0.0
    _, d2 = d1_d2(S, K, T, r, sigma, q)
    disc_k = K * T * math.exp(-r * T)
    return (disc_k * norm_cdf(d2) if right == "C" else -disc_k * norm_cdf(-d2)) / 100.0


@dataclass(frozen=True)
class Greeks:
    price: float
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float

    def scaled(self, qty: float) -> Greeks:
        """The position's greeks: per-share greeks times a signed share quantity."""
        return Greeks(*(qty * v for v in (self.price, self.delta, self.gamma, self.theta, self.vega, self.rho)))

    def __add__(self, other: Greeks) -> Greeks:
        return Greeks(
            self.price + other.price,
            self.delta + other.delta,
            self.gamma + other.gamma,
            self.theta + other.theta,
            self.vega + other.vega,
            self.rho + other.rho,
        )


ZERO_GREEKS = Greeks(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def greeks(S: float, K: float, T: float, r: float, sigma: float, right: str, q: float = 0.0) -> Greeks:
    return Greeks(
        price=bs_price(S, K, T, r, sigma, right, q),
        delta=delta(S, K, T, r, sigma, right, q),
        gamma=gamma(S, K, T, r, sigma, q),
        theta=theta(S, K, T, r, sigma, right, q),
        vega=vega(S, K, T, r, sigma, q),
        rho=rho(S, K, T, r, sigma, right, q),
    )


# ------------------------------------------------------------------
# Implied volatility and strike-for-delta
# ------------------------------------------------------------------


def implied_volatility(
    price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    right: str,
    q: float = 0.0,
    lo: float = 1e-4,
    hi: float = 5.0,
    tol: float = 1e-6,
) -> float | None:
    """
    The sigma at which bs_price equals `price`, or None when no sigma does.

    None means the quote is outside the no-arbitrage band: below the discounted
    intrinsic value (a stale or crossed quote) or above the upper bound. That
    happens on real chains, especially off-hours, so callers must handle it
    rather than trust a clamped number.

    Newton steps on vega, falling back to bisection whenever a step leaves the
    bracket — price is monotonic in sigma, so the bracket always converges.
    """
    right = _right(right)
    if price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return None
    if bs_price(S, K, T, r, lo, right, q) - tol > price or bs_price(S, K, T, r, hi, right, q) < price:
        return None
    sigma = 0.3
    for _ in range(100):
        diff = bs_price(S, K, T, r, sigma, right, q) - price
        if abs(diff) < tol:
            return sigma
        if diff > 0:
            hi = sigma
        else:
            lo = sigma
        v = vega(S, K, T, r, sigma, q) * 100.0  # per unit sigma
        step = sigma - diff / v if v > 1e-12 else None
        sigma = step if step is not None and lo < step < hi else 0.5 * (lo + hi)
    return sigma


def strike_for_delta(
    S: float, T: float, r: float, sigma: float, target_delta: float, right: str, q: float = 0.0
) -> float:
    """
    The (continuous) strike whose delta is `target_delta` — e.g. 0.70 for a
    LEAP call, 0.16 for an iron condor's short strikes. Puts take the absolute
    value (0.30 means delta -0.30). Round to the chain's strike grid yourself.
    """
    right = _right(right)
    d = abs(target_delta)
    if not 0 < d < math.exp(-q * T):
        raise ValueError(f"target delta {target_delta} out of range")
    n_d1 = d * math.exp(q * T)
    d1 = _N.inv_cdf(n_d1) if right == "C" else _N.inv_cdf(1.0 - n_d1)
    return S * math.exp(-(d1 * sigma * math.sqrt(T)) + (r - q + 0.5 * sigma * sigma) * T)


# ------------------------------------------------------------------
# Volatility: historical vs implied
# ------------------------------------------------------------------


def rolling_historical_volatility(
    close: pd.Series, window: int = 20, periods_per_year: int = TRADING_DAYS_PER_YEAR
) -> pd.Series:
    """Annualised close-to-close volatility of log returns over a rolling window."""
    s = pd.Series(close, dtype=float)
    log_ret = np.log(s / s.shift(1))
    return log_ret.rolling(window).std() * math.sqrt(periods_per_year)


def historical_volatility(close: pd.Series, window: int = 20, periods_per_year: int = TRADING_DAYS_PER_YEAR) -> float:
    """The latest value of rolling_historical_volatility (NaN when too short)."""
    hv = rolling_historical_volatility(close, window, periods_per_year).dropna()
    return float(hv.iloc[-1]) if len(hv) else float("nan")


def iv_hv_ratio(iv: float | None, hv: float | None) -> float | None:
    """
    Implied over historical volatility. The course rule: sell premium when this
    is well above 1 (options are pricing more movement than the stock delivers),
    buy options when it is at or below 1.
    """
    if iv is None or hv is None or not hv > 0 or math.isnan(iv) or math.isnan(hv):
        return None
    return iv / hv


def iv_rank(history: Iterable[float], current: float) -> float:
    """Where `current` sits in the history's min..max range, 0-100."""
    values = [v for v in history if v is not None and not math.isnan(v)]
    if not values:
        return float("nan")
    lo, hi = min(values), max(values)
    if hi <= lo:
        return 50.0
    return max(0.0, min(100.0, (current - lo) / (hi - lo) * 100.0))


def iv_percentile(history: Iterable[float], current: float) -> float:
    """Share of the history strictly below `current`, 0-100."""
    values = [v for v in history if v is not None and not math.isnan(v)]
    if not values:
        return float("nan")
    return 100.0 * sum(v < current for v in values) / len(values)


def expected_move(S: float, iv: float, days: float) -> float:
    """One standard deviation of price move over `days` calendar days."""
    return S * iv * math.sqrt(max(days, 0.0) / DAYS_PER_YEAR)


def probability_itm(S: float, K: float, T: float, r: float, sigma: float, right: str, q: float = 0.0) -> float:
    """Risk-neutral probability the option finishes in the money: N(d2) / N(-d2)."""
    right = _right(right)
    if T <= 0 or sigma <= 0:
        return 1.0 if intrinsic(S, K, right) > 0 else 0.0
    _, d2 = d1_d2(S, K, T, r, sigma, q)
    return norm_cdf(d2) if right == "C" else norm_cdf(-d2)


def breakeven(strike: float, premium: float, right: str) -> float:
    """Underlying price at expiry where a long option pays back its premium."""
    return strike + premium if _right(right) == "C" else strike - premium


# ------------------------------------------------------------------
# Multi-leg payoff: spreads, condors, anything with fixed strikes
# ------------------------------------------------------------------


@dataclass(frozen=True)
class Leg:
    """
    One option leg of a position. `qty` is signed (long > 0, short < 0) in any
    consistent unit — contracts or share-equivalents — and every result scales
    with it. `premium` is the per-unit price paid (long) or received (short),
    always entered as a positive number.
    """

    right: str
    strike: float
    qty: float
    premium: float = 0.0


def payoff_at_expiry(legs: Sequence[Leg], S: float) -> float:
    """P&L at expiry with the underlying at S, net of the premiums."""
    return sum(leg.qty * (intrinsic(S, leg.strike, leg.right) - leg.premium) for leg in legs)


def _upside_slope(legs: Sequence[Leg]) -> float:
    """dP&L/dS above every strike: only the calls still move."""
    return sum(leg.qty for leg in legs if _right(leg.right) == "C")


def _kinks(legs: Sequence[Leg]) -> list[float]:
    return sorted({0.0, *(float(leg.strike) for leg in legs)})


def max_loss(legs: Sequence[Leg]) -> float:
    """
    Worst-case loss at expiry, as a positive number (0 if the position cannot
    lose). math.inf when unbounded, i.e. net short calls. The P&L is piecewise
    linear with kinks only at strikes, so its minimum is at a kink or at infinity.
    """
    if not legs:
        return 0.0
    if _upside_slope(legs) < 0:
        return math.inf
    worst = min(payoff_at_expiry(legs, s) for s in _kinks(legs))
    return max(0.0, -worst)


def max_profit(legs: Sequence[Leg]) -> float:
    """Best-case P&L at expiry. math.inf when net long calls."""
    if not legs:
        return 0.0
    if _upside_slope(legs) > 0:
        return math.inf
    return max(payoff_at_expiry(legs, s) for s in _kinks(legs))


def breakevens(legs: Sequence[Leg]) -> list[float]:
    """Every underlying price at expiry where the position's P&L crosses zero."""
    if not legs:
        return []
    points = _kinks(legs)
    values = [payoff_at_expiry(legs, s) for s in points]
    roots: list[float] = []
    for (a, fa), (b, fb) in pairwise(zip(points, values, strict=True)):
        if fa == 0:
            roots.append(a)
        elif fa * fb < 0:
            roots.append(a + (b - a) * fa / (fa - fb))
    top, f_top = points[-1], values[-1]
    slope = _upside_slope(legs)
    if f_top == 0:
        roots.append(top)
    elif slope != 0 and -f_top / slope > 0:
        roots.append(top - f_top / slope)
    return sorted({round(x, 6) for x in roots})


def _prob_below(x: float, S: float, T: float, r: float, sigma: float, q: float) -> float:
    """Risk-neutral P(S_T < x) under the lognormal model."""
    if x <= 0:
        return 0.0
    if math.isinf(x):
        return 1.0
    z = (math.log(x / S) - (r - q - 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return norm_cdf(z)


def probability_of_profit(legs: Sequence[Leg], S: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    """Risk-neutral probability the position finishes with P&L > 0."""
    if T <= 0 or sigma <= 0:
        return 1.0 if payoff_at_expiry(legs, S) > 0 else 0.0
    bounds = [0.0, *breakevens(legs), math.inf]
    total = 0.0
    for lo, hi in pairwise(bounds):
        mid = lo + 1.0 if math.isinf(hi) else 0.5 * (lo + hi)
        if payoff_at_expiry(legs, mid) > 0:
            total += _prob_below(hi, S, T, r, sigma, q) - _prob_below(lo, S, T, r, sigma, q)
    return total


# ------------------------------------------------------------------
# Exposure and liquidity
# ------------------------------------------------------------------


def beta(returns: pd.Series, bench_returns: pd.Series) -> float:
    """cov(r, r_bench) / var(r_bench) over the dates both series share."""
    df = pd.concat([returns, bench_returns], axis=1, join="inner").dropna()
    if len(df) < 3:
        return float("nan")
    var = df.iloc[:, 1].var()
    return float(df.iloc[:, 0].cov(df.iloc[:, 1]) / var) if var > 0 else float("nan")


def beta_weighted_dollars(positions: Iterable[tuple[float, float]]) -> float:
    """
    Index-equivalent dollars of a book of (signed_usd, beta) positions.
    Course example: $60 TSLA long at beta 1.89 acts like $113.4 of index, $40
    KO short at beta 0.6 like -$24, so the book behaves like $89.4 of index.
    """
    return sum(usd * b for usd, b in positions)


def beta_exposure(positions: Iterable[tuple[float, float]]) -> float:
    """beta_weighted_dollars over gross exposure: 0.894 for the course example."""
    positions = list(positions)
    gross = sum(abs(usd) for usd, _ in positions)
    return beta_weighted_dollars(positions) / gross if gross else 0.0


def delta_dollars(option_delta: float, S: float, share_qty: float) -> float:
    """Stock-equivalent exposure of an option position: delta x spot x shares."""
    return option_delta * S * share_qty


def option_dollar_volume(last: float, volume: float) -> float:
    """Premium traded today: last x 100 x contracts. A liquidity screen."""
    return last * CONTRACT_MULTIPLIER * volume


def spread_pct(bid: float, ask: float) -> float:
    """Bid/ask spread as a fraction of the mid (inf without a two-sided market)."""
    if bid <= 0 or ask <= 0 or ask < bid:
        return math.inf
    return (ask - bid) / ((ask + bid) / 2.0)
