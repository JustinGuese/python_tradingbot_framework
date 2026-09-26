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
# Fair volatility: what an option "should" cost
# ------------------------------------------------------------------
#
# Pricing an option with the market's own implied vol reproduces the market
# price exactly, so "mispriced" needs an independent vol: a forecast of the
# diffusion vol the stock will actually deliver, plus the earnings jump when a
# report falls inside the expiry. IV minus that fair vol is the mispricing.


def log_returns(close: pd.Series) -> pd.Series:
    s = pd.Series(close, dtype=float)
    return np.log(s / s.shift(1)).dropna()


def ewma_volatility(returns: pd.Series, lam: float = 0.94, periods_per_year: int = TRADING_DAYS_PER_YEAR) -> float:
    """RiskMetrics exponentially weighted vol of the latest day, annualised."""
    r = pd.Series(returns, dtype=float).dropna()
    if r.empty:
        return float("nan")
    var = float((r**2).ewm(alpha=1 - lam, adjust=False).mean().iloc[-1])
    return math.sqrt(var * periods_per_year)


def har_rv_forecast(
    returns: pd.Series,
    horizon_days: int,
    exclude: Iterable | None = None,
    min_obs: int = 250,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """
    Annualised vol forecast over the next `horizon_days` trading days, from a
    HAR-RV regression (Corsi 2009): the forward mean squared return regressed
    on the last 1-, 5- and 22-day mean squared returns. Fit by OLS on the past
    only (the last `horizon_days` rows have no target yet and drop out).

    `exclude` holds index labels (earnings-reaction days) removed before
    fitting, so the result is diffusion vol and the earnings jump can be added
    separately. With less than `min_obs` of history it returns the plain
    long-run vol. The prediction is floored at a tenth of the long-run
    variance: a linear HAR can extrapolate below zero after a calm stretch.
    """
    r = pd.Series(returns, dtype=float).dropna()
    if exclude is not None:
        r = r[~r.index.isin(list(exclude))]
    sq = r**2
    long_run = float(sq.mean()) if len(sq) else float("nan")
    h = max(int(horizon_days), 1)
    if len(sq) < max(min_obs, 22 + h + 10):
        return math.sqrt(long_run * periods_per_year) if long_run == long_run else float("nan")
    x = np.column_stack(
        [np.ones(len(sq)), sq.to_numpy(), sq.rolling(5).mean().to_numpy(), sq.rolling(22).mean().to_numpy()]
    )
    # Mean of the next h squared returns, aligned to today.
    target = sq[::-1].rolling(h).mean()[::-1].shift(-1).to_numpy()
    ok = ~np.isnan(x).any(axis=1) & ~np.isnan(target)
    coef, *_ = np.linalg.lstsq(x[ok], target[ok], rcond=None)
    pred = float(x[-1] @ coef)
    return math.sqrt(max(pred, 0.1 * long_run) * periods_per_year)


def earnings_reaction_returns(close: pd.Series, report_dates: Iterable[date], after_close: bool = True) -> pd.Series:
    """
    Log return of the session that reacts to each earnings report, indexed by
    that session. `after_close` reports (AAPL's habit) react the next session;
    pre-market ones the same day. Reports outside the price history are skipped.
    """
    s = pd.Series(close, dtype=float).dropna()
    idx = pd.DatetimeIndex(s.index).tz_localize(None).normalize() if len(s) else pd.DatetimeIndex([])
    s.index = idx
    out = {}
    for d in sorted(set(report_dates)):
        ts = pd.Timestamp(d)
        pos = idx.searchsorted(ts, side="right" if after_close else "left")
        if 1 <= pos < len(idx):
            out[idx[pos]] = math.log(s.iloc[pos] / s.iloc[pos - 1])
    return pd.Series(out, dtype=float)


def earnings_jump(moves: Iterable[float]) -> float:
    """Root-mean-square earnings-day move: the jump sigma to add to diffusion vol."""
    values = [float(m) for m in moves if m is not None and not math.isnan(m)]
    return math.sqrt(sum(m * m for m in values) / len(values)) if values else float("nan")


def implied_earnings_move(iv_event: float, T_event: float, iv_base: float, T_base: float | None = None) -> float:
    """
    The one-day earnings move an option chain is pricing.

    With T_base=None, `iv_base` is the vol of an expiry that does NOT span the
    report: the jump is the event expiry's total variance minus what it would
    carry at that base vol. With T_base, both expiries span the report (the
    usual case: a front and a back month), and the diffusion vol is backed out
    of the term structure first — the same jump sits in both, so
    sigma_d^2 = (iv_b^2 T_b - iv_e^2 T_e) / (T_b - T_e).
    """
    if T_base is not None:
        if T_base <= T_event:
            raise ValueError("T_base must be after T_event")
        diffusion_var = max((iv_base * iv_base * T_base - iv_event * iv_event * T_event) / (T_base - T_event), 0.0)
        return math.sqrt(max(iv_event * iv_event * T_event - diffusion_var * T_event, 0.0))
    return math.sqrt(max(iv_event * iv_event * T_event - iv_base * iv_base * T_event, 0.0))


def fair_volatility(sigma_diffusion: float, T: float, jump: float = 0.0) -> float:
    """Annualised vol whose variance over T equals diffusion variance plus one jump."""
    if T <= 0:
        return sigma_diffusion
    return math.sqrt(sigma_diffusion * sigma_diffusion + (jump * jump if jump == jump else 0.0) / T)


def binomial_price(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    right: str,
    q: float = 0.0,
    steps: int = 200,
    american: bool = True,
) -> float:
    """
    Cox-Ross-Rubinstein tree. With american=True every node may exercise early,
    which is what a US equity put is worth; Black-Scholes misses that premium
    for deep ITM puts. Converges to bs_price when american=False.
    """
    right = _right(right)
    if T <= 0 or sigma <= 0:
        return bs_price(S, K, T, r, sigma, right, q)
    dt = T / steps
    u = math.exp(sigma * math.sqrt(dt))
    d = 1.0 / u
    p = (math.exp((r - q) * dt) - d) / (u - d)
    disc = math.exp(-r * dt)
    spots = S * u ** np.arange(steps, -steps - 1, -2, dtype=float)
    payoff = np.maximum(spots - K, 0.0) if right == "C" else np.maximum(K - spots, 0.0)
    values = payoff
    for i in range(steps - 1, -1, -1):
        values = disc * (p * values[:-1] + (1.0 - p) * values[1:])
        if american:
            spots = S * u ** np.arange(i, -i - 1, -2, dtype=float)
            exercise = np.maximum(spots - K, 0.0) if right == "C" else np.maximum(K - spots, 0.0)
            values = np.maximum(values, exercise)
    return float(values[0])


def implied_volatility_american(
    price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    right: str,
    q: float = 0.0,
    steps: int = 100,
    lo: float = 1e-3,
    hi: float = 3.0,
    tol: float = 1e-5,
) -> float | None:
    """Implied vol under the American binomial tree, by bisection. None outside the bounds."""
    if price <= 0 or T <= 0:
        return None
    f_lo = binomial_price(S, K, T, r, lo, right, q, steps) - price
    f_hi = binomial_price(S, K, T, r, hi, right, q, steps) - price
    if f_lo > tol or f_hi < 0:
        return None
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if binomial_price(S, K, T, r, mid, right, q, steps) > price:
            hi = mid
        else:
            lo = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)


def smile_z(S: float, K: float, T: float, atm_iv: float) -> float:
    """Moneyness in standard deviations: ln(K/S) / (atm_iv * sqrt(T))."""
    return math.log(K / S) / (atm_iv * math.sqrt(T))


def fit_smile(z: Sequence[float], iv: Sequence[float]) -> tuple[np.ndarray, np.ndarray]:
    """
    Quadratic smile iv = a + b*z + c*z^2 by least squares. Returns (coefficients
    highest power first, residuals iv - fit). A contract with a large residual
    is rich (+) or cheap (-) against its neighbours on the same expiry.
    """
    z_arr, iv_arr = np.asarray(z, dtype=float), np.asarray(iv, dtype=float)
    if len(z_arr) < 4:
        raise ValueError("fit_smile needs at least 4 points")
    coef = np.polyfit(z_arr, iv_arr, 2)
    return coef, iv_arr - np.polyval(coef, z_arr)


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


def stock_leg(shares: float, cost: float = 0.0) -> Leg:
    """
    Shares of the underlying as a leg: a share is a call struck at 0, so the
    payoff math covers covered calls, collars and hedged straddles unchanged.
    """
    return Leg("C", 0.0, shares, cost)


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
