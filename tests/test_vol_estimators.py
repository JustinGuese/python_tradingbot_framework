"""
vol_estimators: OHLC range estimators checked against simulated GBM paths with
known volatility, HAR-RV and GARCH(1,1) checked against their own long-run
targets, plus the small pure-math helpers (VRP z-score, Whalley-Wilmott band).
"""

import math

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils import vol_estimators as ve

SIGMA_TRUE = 0.25
N_DAYS = 2000
# A real trading day has ~390 one-minute bars; range-estimator bias from
# discrete sampling shrinks as this grows, and 390 keeps it comfortably under
# the ~10% test tolerance while staying fast (numpy-vectorised per day).
STEPS_PER_DAY = 390
TRADING_DAYS = 252
# Overnight gaps are real but small relative to the day's own range, so this
# stays modest: big enough to exercise Yang-Zhang's overnight term, small
# enough that Parkinson/GK/RS (which structurally can't see the gap) still
# land within tolerance of the true vol.
OVERNIGHT_FRAC = 0.05


def simulate_ohlc(seed: int, sigma: float = SIGMA_TRUE, drift: float = 0.0, n_days: int = N_DAYS) -> pd.DataFrame:
    """
    A GBM price path split into overnight gaps (close_t-1 -> open_t) and
    STEPS_PER_DAY intraday steps (open_t -> ... -> close_t), so high/low carry
    real information beyond the close, the way real OHLC bars do. Vectorised
    per day for speed; only the (cheap) per-day max/min loop stays in Python.
    """
    rng = np.random.default_rng(seed)
    dt_day = 1.0 / TRADING_DAYS
    var_day = sigma**2 * dt_day
    var_on = OVERNIGHT_FRAC * var_day
    var_intraday_total = (1.0 - OVERNIGHT_FRAC) * var_day
    var_step = var_intraday_total / STEPS_PER_DAY
    mu_day = drift * dt_day
    mu_on = OVERNIGHT_FRAC * mu_day
    mu_step = (1.0 - OVERNIGHT_FRAC) * mu_day / STEPS_PER_DAY

    overnight = rng.normal(mu_on, math.sqrt(var_on), size=n_days)
    intraday = rng.normal(mu_step, math.sqrt(var_step), size=(n_days, STEPS_PER_DAY))
    intraday_cum = np.cumsum(intraday, axis=1)  # relative to that day's open, length STEPS_PER_DAY

    opens = np.empty(n_days)
    highs = np.empty(n_days)
    lows = np.empty(n_days)
    closes = np.empty(n_days)

    log_price = math.log(100.0)
    for d in range(n_days):
        log_price += overnight[d]
        opens[d] = log_price
        day_path = log_price + np.concatenate(([0.0], intraday_cum[d]))  # open ... close, length +1
        highs[d] = day_path.max()
        lows[d] = day_path.min()
        log_price = day_path[-1]
        closes[d] = log_price

    idx = pd.bdate_range("2015-01-01", periods=n_days)
    return pd.DataFrame(
        {"open": np.exp(opens), "high": np.exp(highs), "low": np.exp(lows), "close": np.exp(closes)}, index=idx
    )


@pytest.fixture(scope="module")
def flat_ohlc() -> pd.DataFrame:
    return simulate_ohlc(seed=1, drift=0.0)


@pytest.fixture(scope="module")
def drifting_ohlc() -> pd.DataFrame:
    # A strong annualised drift (100%/yr) — enough to bias drift-sensitive estimators.
    return simulate_ohlc(seed=2, drift=1.0)


def _ann_vol_from_daily_var(daily_var: pd.Series) -> float:
    return math.sqrt(float(daily_var.dropna().mean()) * TRADING_DAYS)


# ------------------------------------------------------------------
# OHLC range estimators
# ------------------------------------------------------------------


def test_parkinson_recovers_true_vol(flat_ohlc):
    vol = _ann_vol_from_daily_var(ve.parkinson_daily_variance(flat_ohlc))
    assert vol == pytest.approx(SIGMA_TRUE, rel=0.10)


def test_garman_klass_recovers_true_vol(flat_ohlc):
    vol = _ann_vol_from_daily_var(ve.garman_klass_daily_variance(flat_ohlc))
    assert vol == pytest.approx(SIGMA_TRUE, rel=0.10)


def test_rogers_satchell_recovers_true_vol(flat_ohlc):
    vol = _ann_vol_from_daily_var(ve.rogers_satchell_daily_variance(flat_ohlc))
    assert vol == pytest.approx(SIGMA_TRUE, rel=0.10)


def test_yang_zhang_daily_variance_recovers_true_vol(flat_ohlc):
    vol = _ann_vol_from_daily_var(ve.yang_zhang_daily_variance(flat_ohlc))
    assert vol == pytest.approx(SIGMA_TRUE, rel=0.10)


def test_yang_zhang_variance_rolling_recovers_true_vol(flat_ohlc):
    vol_series = ve.yang_zhang_volatility(flat_ohlc, window=21).dropna()
    assert len(vol_series) > 0
    assert float(vol_series.mean()) == pytest.approx(SIGMA_TRUE, rel=0.10)


def test_yang_zhang_variance_window_too_small_raises(flat_ohlc):
    with pytest.raises(ValueError):
        ve.yang_zhang_variance(flat_ohlc, window=1)


def test_rogers_satchell_and_yang_zhang_are_drift_robust(drifting_ohlc):
    """
    Parkinson/GK assume zero drift; Rogers-Satchell and Yang-Zhang are built
    to be drift-independent (that's their whole point), so under a strong
    100%/yr drift they should still land close to the true diffusion vol.
    """
    rs_vol = _ann_vol_from_daily_var(ve.rogers_satchell_daily_variance(drifting_ohlc))
    yz_vol = _ann_vol_from_daily_var(ve.yang_zhang_daily_variance(drifting_ohlc))
    assert rs_vol == pytest.approx(SIGMA_TRUE, rel=0.15)
    assert yz_vol == pytest.approx(SIGMA_TRUE, rel=0.15)

    yz_rolling = ve.yang_zhang_volatility(drifting_ohlc, window=21).dropna()
    assert float(yz_rolling.mean()) == pytest.approx(SIGMA_TRUE, rel=0.15)


def test_missing_columns_raise_keyerror():
    bad = pd.DataFrame({"open": [1.0, 2.0], "high": [1.5, 2.5]})
    with pytest.raises(KeyError):
        ve.parkinson_daily_variance(bad)


# ------------------------------------------------------------------
# HAR-RV forecast from a supplied daily-variance series
# ------------------------------------------------------------------


def test_har_forecast_from_daily_variance_recovers_true_vol(flat_ohlc):
    daily_var = ve.yang_zhang_daily_variance(flat_ohlc)
    forecast = ve.har_forecast_from_daily_variance(daily_var, horizon_days=5)
    assert forecast == pytest.approx(SIGMA_TRUE, rel=0.20)


def test_har_forecast_from_daily_variance_short_series_falls_back_to_long_run():
    idx = pd.bdate_range("2020-01-01", periods=50)
    daily_var = pd.Series(np.full(50, (SIGMA_TRUE**2) / TRADING_DAYS), index=idx)
    forecast = ve.har_forecast_from_daily_variance(daily_var, horizon_days=5, min_obs=250)
    expected = math.sqrt(float(daily_var.mean()) * TRADING_DAYS)
    assert forecast == pytest.approx(expected, rel=1e-9)


def test_har_forecast_from_daily_variance_exclude_runs_and_stays_finite(flat_ohlc):
    daily_var = ve.yang_zhang_daily_variance(flat_ohlc)
    exclude_dates = daily_var.dropna().index[::50]  # drop every 50th day, arbitrary "earnings" days
    forecast = ve.har_forecast_from_daily_variance(daily_var, horizon_days=5, exclude=exclude_dates)
    assert math.isfinite(forecast)
    assert forecast > 0


def test_har_forecast_from_daily_variance_empty_series_is_nan():
    forecast = ve.har_forecast_from_daily_variance(pd.Series([], dtype=float), horizon_days=5)
    assert math.isnan(forecast)


# ------------------------------------------------------------------
# GARCH(1,1)
# ------------------------------------------------------------------

GARCH_OMEGA = 1e-6
GARCH_ALPHA = 0.08
GARCH_BETA = 0.90


def simulate_garch11(seed: int, omega=GARCH_OMEGA, alpha=GARCH_ALPHA, beta=GARCH_BETA, n=3000) -> pd.Series:
    rng = np.random.default_rng(seed)
    long_run_var = omega / (1.0 - alpha - beta)
    returns = np.empty(n)
    var = long_run_var
    for t in range(n):
        returns[t] = rng.normal(0.0, math.sqrt(var))
        var = omega + alpha * returns[t] ** 2 + beta * var
    return pd.Series(returns)


@pytest.fixture(scope="module")
def garch_returns() -> pd.Series:
    return simulate_garch11(seed=3)


def test_garch11_fit_recovers_persistence(garch_returns):
    omega, alpha, beta = ve.garch11_fit(garch_returns)
    assert omega > 0
    assert 0.0 <= alpha < 1.0
    assert 0.0 <= beta < 1.0
    true_persistence = GARCH_ALPHA + GARCH_BETA
    fitted_persistence = alpha + beta
    assert fitted_persistence == pytest.approx(true_persistence, abs=0.05)


def test_garch11_fit_short_series_uses_fallback():
    short = pd.Series(np.random.default_rng(4).normal(0, 0.01, size=10))
    omega, alpha, beta = ve.garch11_fit(short)
    assert alpha == pytest.approx(0.05)
    assert beta == pytest.approx(0.90)
    assert omega > 0


def test_garch11_forecast_converges_to_long_run_vol_for_long_horizon(garch_returns):
    omega, alpha, beta = ve.garch11_fit(garch_returns)
    long_run_vol = math.sqrt(omega / (1.0 - alpha - beta) * TRADING_DAYS)
    long_horizon_forecast = ve.garch11_forecast(garch_returns, horizon_days=500)
    assert long_horizon_forecast == pytest.approx(long_run_vol, rel=0.05)


def test_garch11_forecast_short_series_falls_back_to_sample_vol():
    r = pd.Series(np.random.default_rng(5).normal(0, 0.02, size=20))
    forecast = ve.garch11_forecast(r, horizon_days=10, min_obs=250)
    expected = float(r.std()) * math.sqrt(TRADING_DAYS)
    assert forecast == pytest.approx(expected, rel=1e-9)


# ------------------------------------------------------------------
# Variance risk premium z-score
# ------------------------------------------------------------------


def test_vrp_zscore_none_below_min_obs():
    history = [0.01 * i for i in range(10)]
    assert ve.vrp_zscore(history, current=0.5, min_obs=60) is None


def test_vrp_zscore_known_value():
    history = list(range(100))  # 0..99
    current = 120.0
    result = ve.vrp_zscore(history, current=current, min_obs=60, lookback=252)
    s = pd.Series(history, dtype=float)
    expected = (current - s.mean()) / s.std()
    assert result == pytest.approx(expected)


def test_vrp_zscore_none_when_std_zero():
    history = [1.0] * 100
    assert ve.vrp_zscore(history, current=1.0, min_obs=60) is None


def test_vrp_zscore_respects_lookback_window():
    # Old values far outside the lookback window shouldn't move the z-score.
    history = [1000.0] * 500 + list(range(100))
    result_full = ve.vrp_zscore(history, current=120.0, min_obs=60, lookback=100)
    tail_only = list(range(100))
    s = pd.Series(tail_only, dtype=float)
    expected = (120.0 - s.mean()) / s.std()
    assert result_full == pytest.approx(expected)


# ------------------------------------------------------------------
# Whalley-Wilmott no-transact band
# ------------------------------------------------------------------


def test_ww_band_grows_with_cost():
    bands = [ve.whalley_wilmott_band(S=100.0, gamma=0.5, cost_frac=c, risk_aversion=1.0) for c in (0.001, 0.005, 0.01)]
    assert bands == sorted(bands)
    assert bands[0] < bands[-1]


def test_ww_band_shrinks_with_risk_aversion():
    bands = [
        ve.whalley_wilmott_band(S=100.0, gamma=0.5, cost_frac=0.01, risk_aversion=ra) for ra in (0.5, 1.0, 2.0, 5.0)
    ]
    assert bands == sorted(bands, reverse=True)
    assert bands[0] > bands[-1]


def test_ww_band_invalid_inputs_raise():
    with pytest.raises(ValueError):
        ve.whalley_wilmott_band(S=100.0, gamma=0.5, cost_frac=-0.01, risk_aversion=1.0)
    with pytest.raises(ValueError):
        ve.whalley_wilmott_band(S=100.0, gamma=0.5, cost_frac=0.01, risk_aversion=0.0)


@pytest.mark.parametrize(
    "net_delta,band,expected",
    [
        (50.0, 100.0, None),
        (-50.0, 100.0, None),
        (0.0, 100.0, None),
        (150.0, 100.0, -50.0),
        (-150.0, 100.0, 50.0),
        (100.0, 100.0, None),  # exactly on the edge: no rehedge needed
    ],
)
def test_ww_rehedge_target(net_delta, band, expected):
    result = ve.ww_rehedge_target(net_delta, band)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_ww_rehedge_target_negative_band_raises():
    with pytest.raises(ValueError):
        ve.ww_rehedge_target(150.0, -1.0)
