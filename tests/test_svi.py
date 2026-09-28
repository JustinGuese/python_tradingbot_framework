import math

import numpy as np
import pytest

from tradingbot.utils.svi import (
    SVIParams,
    butterfly_arbitrage_free,
    butterfly_density,
    calendar_arbitrage_free,
    fit_svi,
    half_spread_in_vol,
    svi_outliers,
)


def test_fit_svi_recovers_known_params_from_noiseless_data():
    true_params = SVIParams(a=0.04, b=0.1, rho=-0.3, m=0.0, sigma=0.3)
    k = np.linspace(-1.0, 1.0, 25)
    w = true_params.w(k)

    fitted = fit_svi(k, w)

    assert fitted is not None
    grid = np.linspace(-1.0, 1.0, 200)
    # Compare the curve, not the raw parameters: SVI is only weakly
    # identifiable, several parameter sets can trace ~the same w(k).
    assert np.max(np.abs(fitted.w(grid) - true_params.w(grid))) < 1e-6


def test_fit_svi_needs_at_least_five_points():
    k = np.linspace(-0.5, 0.5, 4)
    w = 0.04 + 0.01 * k**2

    assert fit_svi(k, w) is None


def test_fit_svi_none_on_mismatched_shapes():
    with pytest.raises(ValueError):
        fit_svi([1.0, 2.0, 3.0, 4.0, 5.0], [1.0, 2.0])


def test_butterfly_arbitrage_free_smile_passes():
    # Verified directly: butterfly_density's minimum over a dense k-grid is
    # comfortably positive (~0.34) for these parameters, and w > 0 throughout.
    params = SVIParams(a=0.04, b=0.1, rho=-0.3, m=0.0, sigma=0.3)

    assert butterfly_arbitrage_free(params) is True


def test_butterfly_arbitrage_free_flags_a_genuine_violation():
    # Very large b with sigma tiny and rho close to -1 makes the smile far too
    # curved/skewed to stay convex in strike; confirmed the density really
    # does go negative (min g ~ -27 at k ~ -0.02), not merely w <= 0.
    params = SVIParams(a=0.01, b=2.0, rho=-0.9, m=0.0, sigma=0.01)
    grid = np.linspace(-1.5, 1.5, 301)

    assert np.all(params.w(grid) > 0)  # failure is from g < 0, not w <= 0
    g = butterfly_density(params, grid)
    assert np.min(g) < 0  # the arbitrage is real

    assert butterfly_arbitrage_free(params) is False


def test_calendar_arbitrage_free_passes_when_total_variance_increases():
    front = SVIParams(a=0.02, b=0.1, rho=-0.3, m=0.0, sigma=0.3)
    back = SVIParams(a=0.05, b=0.1, rho=-0.3, m=0.0, sigma=0.3)  # strictly larger a -> w2(k) > w1(k) everywhere

    assert calendar_arbitrage_free([(30 / 365, front), (60 / 365, back)]) is True


def test_calendar_arbitrage_free_fails_when_later_expiry_has_lower_w():
    front = SVIParams(a=0.05, b=0.1, rho=-0.3, m=0.0, sigma=0.3)
    back = SVIParams(a=0.02, b=0.1, rho=-0.3, m=0.0, sigma=0.3)  # smaller a -> w2(k) < w1(k) everywhere

    assert calendar_arbitrage_free([(30 / 365, front), (60 / 365, back)]) is False


def test_calendar_arbitrage_free_trivially_true_for_one_fit():
    params = SVIParams(a=0.02, b=0.1, rho=-0.3, m=0.0, sigma=0.3)

    assert calendar_arbitrage_free([(30 / 365, params)]) is True


def test_svi_outliers_flags_planted_mispricing_and_skips_well_fit_points():
    forward = 100.0
    tau = 0.5
    params = SVIParams(a=0.02, b=0.05, rho=-0.2, m=0.0, sigma=0.3)
    strikes = [80.0, 90.0, 100.0, 110.0, 120.0, 130.0]
    rights = ["C"] * 6
    fit_ivs = [float(params.iv(math.log(s / forward), tau)) for s in strikes]

    ivs = list(fit_ivs)
    ivs[3] = fit_ivs[3] + 0.05  # plant a mispriced quote at K=110, well past the half-spread
    half_spreads = [0.01] * 6

    outliers = svi_outliers(strikes, rights, ivs, half_spreads, forward, tau, params)

    assert len(outliers) == 1
    assert outliers[0].strike == 110.0
    assert abs(outliers[0].resid - 0.05) < 1e-9


def test_svi_outliers_skips_nan_inputs():
    forward = 100.0
    tau = 0.5
    params = SVIParams(a=0.02, b=0.05, rho=-0.2, m=0.0, sigma=0.3)
    strikes = [90.0, 100.0, 110.0]
    rights = ["C", "C", "C"]
    ivs = [float("nan"), 0.26, 0.99]
    half_spreads = [0.01, 0.01, float("nan")]

    outliers = svi_outliers(strikes, rights, ivs, half_spreads, forward, tau, params)

    assert all(o.strike not in (90.0, 110.0) for o in outliers)


def test_svi_outliers_sorted_by_abs_resid_descending():
    forward = 100.0
    tau = 0.5
    params = SVIParams(a=0.02, b=0.05, rho=-0.2, m=0.0, sigma=0.3)
    strikes = [90.0, 100.0, 110.0]
    rights = ["C", "C", "C"]
    fit_ivs = [float(params.iv(math.log(s / forward), tau)) for s in strikes]
    ivs = [fit_ivs[0] + 0.08, fit_ivs[1] + 0.03, fit_ivs[2]]
    half_spreads = [0.01, 0.01, 0.01]

    outliers = svi_outliers(strikes, rights, ivs, half_spreads, forward, tau, params)

    assert [o.strike for o in outliers] == [90.0, 100.0]
    assert abs(outliers[0].resid) >= abs(outliers[1].resid)


def test_half_spread_in_vol_arithmetic():
    # vega_per_point is price change per 0.01 vol (option_math.vega's convention),
    # so price-per-1.00-vol is vega_per_point * 100.
    # half spread price = (1.5 - 1.0) / 2 = 0.25; price-per-vol = 0.2 * 100 = 20
    # -> 0.25 / 20 = 0.0125
    assert math.isclose(half_spread_in_vol(1.0, 1.5, 0.2), 0.0125, rel_tol=1e-9)


def test_half_spread_in_vol_nan_on_invalid_inputs():
    assert math.isnan(half_spread_in_vol(1.5, 1.0, 0.2))  # crossed quote
    assert math.isnan(half_spread_in_vol(1.0, 1.5, 0.0))  # zero vega
    assert math.isnan(half_spread_in_vol(1.0, 1.5, -0.1))  # negative vega
    assert math.isnan(half_spread_in_vol(1.0, float("nan"), 0.2))
    assert math.isnan(half_spread_in_vol(1.0, 1.5, None))
