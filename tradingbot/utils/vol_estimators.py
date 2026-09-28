"""
Volatility estimators beyond close-to-close: OHLC range estimators, a HAR-RV
forecast built on top of any daily-variance series, a GARCH(1,1) forecast, a
variance-risk-premium z-score, and the Whalley-Wilmott no-transact band for
gamma hedging.

Pure functions only: no database, no network. Mirrors the conventions in
`option_math.py` — annualise with a `periods_per_year` parameter (default 252
trading days), work on `pd.Series`/`pd.DataFrame` for time series, plain
floats for scalars, and fall back sanely rather than raising when there is
too little data.

OHLC range estimators (Parkinson, Garman-Klass, Rogers-Satchell, Yang-Zhang)
use the whole bar's range, not just the close, so they need far fewer days to
reach a given precision than close-to-close historical vol — see Parkinson
(1980), Garman & Klass (1980), Rogers & Satchell (1991), Yang & Zhang (2000).
Every "daily variance" function returns a **per-day, un-annualised** variance
(so `× periods_per_year` gives annualised variance, `sqrt(that)` annualised
vol) — that is the convention `har_forecast_from_daily_variance` expects as
input, exactly like `option_math.har_rv_forecast` expects squared daily
returns.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
import pandas as pd
from scipy.optimize import minimize

TRADING_DAYS_PER_YEAR = 252

_LN2 = math.log(2.0)
_TWO_LN2_MINUS_1 = 2.0 * _LN2 - 1.0


def _ohlc_logs(ohlc: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """ln(H/L), ln(C/O), ln(H/C), ln(L/C) as float Series aligned to ohlc's index."""
    o = pd.Series(ohlc["open"], dtype=float)
    h = pd.Series(ohlc["high"], dtype=float)
    lo = pd.Series(ohlc["low"], dtype=float)
    c = pd.Series(ohlc["close"], dtype=float)
    return np.log(h / lo), np.log(c / o), np.log(h / c), np.log(lo / c)


# ------------------------------------------------------------------
# OHLC range estimators — per-day variance
# ------------------------------------------------------------------


def parkinson_daily_variance(ohlc: pd.DataFrame) -> pd.Series:
    """(ln(H/L))^2 / (4 ln 2): the classic high-low range estimator. Ignores drift and jumps."""
    log_hl, _, _, _ = _ohlc_logs(ohlc)
    return log_hl**2 / (4.0 * _LN2)


def garman_klass_daily_variance(ohlc: pd.DataFrame) -> pd.Series:
    """
    0.5*(ln(H/L))^2 - (2ln2-1)*(ln(C/O))^2: the common "simple" Garman-Klass form.

    The textbook GK estimator (Garman & Klass 1980) adds further overnight-jump
    terms with a 0.511 coefficient in front of the range term; this simpler
    two-term version is what most practitioner references call "Garman-Klass"
    and is what's implemented here.
    """
    log_hl, log_co, _, _ = _ohlc_logs(ohlc)
    return 0.5 * log_hl**2 - _TWO_LN2_MINUS_1 * log_co**2


def rogers_satchell_daily_variance(ohlc: pd.DataFrame) -> pd.Series:
    """ln(H/C)*ln(H/O) + ln(L/C)*ln(L/O): drift-independent, unlike Parkinson/GK."""
    _, _, log_hc, log_lc = _ohlc_logs(ohlc)
    o = pd.Series(ohlc["open"], dtype=float)
    h = pd.Series(ohlc["high"], dtype=float)
    lo = pd.Series(ohlc["low"], dtype=float)
    log_ho = np.log(h / o)
    log_lo = np.log(lo / o)
    return log_hc * log_ho + log_lc * log_lo


def yang_zhang_daily_variance(ohlc: pd.DataFrame) -> pd.Series:
    """
    A per-day Yang-Zhang proxy usable as HAR input: the day's overnight squared
    return (ln(O_t/C_{t-1}))^2 plus the day's Rogers-Satchell term. This is not
    the k-weighted rolling YZ estimator (`yang_zhang_variance` below) — it is a
    single-day variance series with the same three ingredients, meant to be fed
    into a HAR-style regression the way squared returns feed `har_rv_forecast`.
    """
    close = pd.Series(ohlc["close"], dtype=float)
    open_ = pd.Series(ohlc["open"], dtype=float)
    overnight = np.log(open_ / close.shift(1))
    rs = rogers_satchell_daily_variance(ohlc)
    return overnight**2 + rs


def yang_zhang_variance(ohlc: pd.DataFrame, window: int = 21) -> pd.Series:
    """
    Annualised rolling Yang-Zhang variance (Yang & Zhang 2000): handles both
    drift and the opening jump, combining overnight variance, open-to-close
    variance and the Rogers-Satchell term with weight k chosen to minimise
    the estimator's own variance.

    variance = overnight_var + k * open_close_var + (1-k) * mean(RS)
    k = 0.34 / (1.34 + (n+1)/(n-1)), n = window.

    Rolling variances use ddof=1 (n-1) over the trailing `window` days, so the
    first `window` rows are NaN. Result is annualised variance (not vol,
    not per-day) — multiply nothing further, or `sqrt` for vol.
    """
    n = int(window)
    if n < 2:
        raise ValueError("window must be >= 2")
    close = pd.Series(ohlc["close"], dtype=float)
    open_ = pd.Series(ohlc["open"], dtype=float)
    overnight = np.log(open_ / close.shift(1))
    open_close = np.log(close / open_)
    rs = rogers_satchell_daily_variance(ohlc)

    overnight_var = overnight.rolling(n).var(ddof=1)
    open_close_var = open_close.rolling(n).var(ddof=1)
    rs_mean = rs.rolling(n).mean()

    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    daily_var = overnight_var + k * open_close_var + (1.0 - k) * rs_mean
    return daily_var * TRADING_DAYS_PER_YEAR


def yang_zhang_volatility(ohlc: pd.DataFrame, window: int = 21) -> pd.Series:
    """sqrt(yang_zhang_variance): annualised rolling Yang-Zhang volatility."""
    return np.sqrt(yang_zhang_variance(ohlc, window).clip(lower=0.0))


# ------------------------------------------------------------------
# HAR-RV forecast from an arbitrary daily-variance series
# ------------------------------------------------------------------


def har_forecast_from_daily_variance(
    daily_var: pd.Series,
    horizon_days: int,
    exclude: object | None = None,
    min_obs: int = 250,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """
    Annualised vol forecast over the next `horizon_days` trading days, from a
    HAR-RV regression (Corsi 2009) fit on a supplied per-day variance series
    (e.g. `yang_zhang_daily_variance`, or squared returns) rather than on
    squared close-to-close returns directly. Same structure as
    `option_math.har_rv_forecast`: regressors are the 1-, 5- and 22-day
    rolling means of `daily_var`, the target is the forward h-day mean of
    `daily_var`, fit by OLS on rows with a full forward window only.

    `exclude` holds index labels (earnings-reaction days) dropped before
    fitting. With less than `min_obs` of history it returns the plain
    long-run vol. The prediction is floored at a tenth of the long-run
    variance, since a linear HAR can extrapolate below zero after a calm
    stretch.
    """
    v = pd.Series(daily_var, dtype=float).dropna()
    if exclude is not None:
        v = v[~v.index.isin(list(exclude))]
    long_run = float(v.mean()) if len(v) else float("nan")
    h = max(int(horizon_days), 1)
    if len(v) < max(min_obs, 22 + h + 10):
        return math.sqrt(long_run * periods_per_year) if long_run == long_run else float("nan")
    x = np.column_stack(
        [np.ones(len(v)), v.to_numpy(), v.rolling(5).mean().to_numpy(), v.rolling(22).mean().to_numpy()]
    )
    # Mean of the next h daily variances, aligned to today.
    target = v[::-1].rolling(h).mean()[::-1].shift(-1).to_numpy()
    ok = ~np.isnan(x).any(axis=1) & ~np.isnan(target)
    coef, *_ = np.linalg.lstsq(x[ok], target[ok], rcond=None)
    pred = float(x[-1] @ coef)
    return math.sqrt(max(pred, 0.1 * long_run) * periods_per_year)


# ------------------------------------------------------------------
# GARCH(1,1)
# ------------------------------------------------------------------

_FALLBACK_ALPHA = 0.05
_FALLBACK_BETA = 0.90


def _garch_neg_log_likelihood(params: np.ndarray, r: np.ndarray) -> float:
    omega, alpha, beta_ = params
    n = len(r)
    var = np.empty(n)
    var[0] = float(np.var(r)) if n > 1 else omega
    if var[0] <= 0:
        var[0] = 1e-8
    nll = 0.0
    for t in range(n):
        v = var[t]
        if v <= 0:
            v = 1e-12
        nll += 0.5 * (math.log(2.0 * math.pi) + math.log(v) + r[t] * r[t] / v)
        if t + 1 < n:
            var[t + 1] = omega + alpha * r[t] * r[t] + beta_ * v
    penalty = 1e6 * max(0.0, alpha + beta_ - 0.999) ** 2
    return nll + penalty


def garch11_fit(returns: pd.Series) -> tuple[float, float, float]:
    """
    Fit a Gaussian GARCH(1,1) — sigma_t^2 = omega + alpha*r_{t-1}^2 + beta*sigma_{t-1}^2
    — by maximum likelihood (scipy L-BFGS-B on the negative log-likelihood).
    Returns has its mean removed first. Bounds: omega > 0, alpha/beta in
    [0, 1), with a soft penalty keeping alpha+beta < 0.999 for stationarity.

    Falls back to reasonable stylised-fact parameters
    (omega = var*(1-alpha-beta), alpha=0.05, beta=0.90) if the optimiser fails
    to converge or there isn't enough data (< 30 observations) to fit at all.
    """
    r = pd.Series(returns, dtype=float).dropna()
    r = (r - r.mean()).to_numpy()
    if len(r) < 30:
        var = float(np.var(r)) if len(r) else 1e-4
        return var * (1.0 - _FALLBACK_ALPHA - _FALLBACK_BETA), _FALLBACK_ALPHA, _FALLBACK_BETA

    sample_var = float(np.var(r))
    x0 = np.array([sample_var * (1.0 - _FALLBACK_ALPHA - _FALLBACK_BETA), _FALLBACK_ALPHA, _FALLBACK_BETA])
    bounds = [(1e-12, None), (0.0, 0.999), (0.0, 0.999)]
    try:
        result = minimize(_garch_neg_log_likelihood, x0, args=(r,), method="L-BFGS-B", bounds=bounds)
        omega, alpha, beta_ = result.x
        if not result.success or omega <= 0 or not (0.0 <= alpha < 1.0) or not (0.0 <= beta_ < 1.0):
            raise ValueError("garch11_fit: optimisation did not converge to a valid point")
        if alpha + beta_ >= 0.999:
            beta_ = max(0.0, 0.999 - alpha)
        return float(omega), float(alpha), float(beta_)
    except Exception:
        return sample_var * (1.0 - _FALLBACK_ALPHA - _FALLBACK_BETA), _FALLBACK_ALPHA, _FALLBACK_BETA


def garch11_forecast(
    returns: pd.Series, horizon_days: int, periods_per_year: int = TRADING_DAYS_PER_YEAR, min_obs: int = 250
) -> float:
    """
    Annualised vol forecast averaged over the next `horizon_days`, from a
    GARCH(1,1) fit via `garch11_fit`. The h-step-ahead conditional variance
    forecast mean-reverts geometrically toward the long-run variance
    omega/(1-alpha-beta); this returns sqrt of the average of the daily
    forecasts over the horizon, annualised.

    Falls back to plain sample volatility (not a GARCH forecast) if there is
    less than `min_obs` history.
    """
    r = pd.Series(returns, dtype=float).dropna()
    h = max(int(horizon_days), 1)
    if len(r) < min_obs:
        sample_std = float(r.std()) if len(r) > 1 else float("nan")
        return sample_std * math.sqrt(periods_per_year) if sample_std == sample_std else float("nan")

    omega, alpha, beta_ = garch11_fit(r)
    demeaned = (r - r.mean()).to_numpy()
    persistence = alpha + beta_
    long_run = omega / (1.0 - persistence) if persistence < 1.0 else float(np.var(demeaned))

    # Last conditional variance, built the same way as the likelihood recursion.
    var = float(np.var(demeaned)) if len(demeaned) else long_run
    for t in range(len(demeaned) - 1):
        var = omega + alpha * demeaned[t] * demeaned[t] + beta_ * var
    last_resid_sq = demeaned[-1] * demeaned[-1]
    next_var = omega + alpha * last_resid_sq + beta_ * var

    variances = np.empty(h)
    variances[0] = next_var
    for t in range(1, h):
        variances[t] = long_run + persistence**t * (next_var - long_run) if persistence < 1.0 else next_var
    avg_var = float(np.mean(np.maximum(variances, 0.0)))
    return math.sqrt(avg_var * periods_per_year)


# ------------------------------------------------------------------
# Variance risk premium
# ------------------------------------------------------------------


def vrp_zscore(
    spread_history: Sequence[float] | pd.Series, current: float, min_obs: int = 60, lookback: int = 252
) -> float | None:
    """
    z-score of `current` (typically an IV-minus-fair-vol spread) against its
    own trailing history: (current - mean) / std over the last `lookback`
    finite observations. None if there are fewer than `min_obs` finite
    observations, or the std is 0 (no variation to standardise against).
    """
    s = pd.Series(spread_history, dtype=float).dropna()
    s = s.iloc[-int(lookback) :]
    if len(s) < min_obs:
        return None
    std = float(s.std())
    if std == 0.0 or std != std:
        return None
    return (float(current) - float(s.mean())) / std


# ------------------------------------------------------------------
# Whalley-Wilmott no-transact band
# ------------------------------------------------------------------


def whalley_wilmott_band(S: float, gamma: float, cost_frac: float, risk_aversion: float) -> float:
    """
    Whalley & Wilmott (1997) optimal no-transact half-width for delta hedging
    under proportional transaction costs:

        H = (1.5 * cost_frac * S * gamma^2 / risk_aversion) ** (1/3)

    `gamma` is the position's gamma per share-equivalent (i.e. already scaled
    by quantity — see `option_math.Greeks.scaled`); the returned band is in
    delta units (shares): hold net delta anywhere inside +/-H without
    rehedging, and rehedge only to the nearest edge of the band when outside
    it. Wider `cost_frac` or larger `gamma` widens the band (less hedging
    under costs); larger `risk_aversion` narrows it (tighter hedging when
    risk matters more).
    """
    if cost_frac < 0 or risk_aversion <= 0:
        raise ValueError("cost_frac must be >= 0 and risk_aversion must be > 0")
    return (1.5 * cost_frac * abs(S) * gamma * gamma / risk_aversion) ** (1.0 / 3.0)


def ww_rehedge_target(net_delta: float, band: float) -> float | None:
    """
    The share trade that brings `net_delta` back to the nearest edge of
    +/-`band`, or None if it is already inside the band (no rehedge needed).
    E.g. net_delta=150, band=100 -> -50 (sell 50 shares to reach +100).
    """
    if band < 0:
        raise ValueError("band must be >= 0")
    if abs(net_delta) <= band:
        return None
    edge = band if net_delta > 0 else -band
    return edge - net_delta
