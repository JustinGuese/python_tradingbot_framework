"""Tests for the quants.md capped risk-parity bot (pure compute_weights, synthetic data)."""

import numpy as np
import pandas as pd

from tradingbot.quantsmd_risk_parity_capped import (
    CAP,
    LOOKBACK,
    UNIVERSE,
    QuantsmdRiskParityCappedBot,
    compute_weights,
)
from tradingbot.utils.quantsmd_common import SECTORS9

MONTH_FIRST_END = "2024-02-01"  # Thursday; the previous bar is 2024-01-31, a new month.
MID_MONTH_END = "2024-01-17"


def _closes(sigmas: list[float], n: int, end: str = MONTH_FIRST_END, seed: int = 7) -> pd.DataFrame:
    """Business-day close frame, one column per sigma, independent daily noise."""
    rng = np.random.default_rng(seed)
    index = pd.bdate_range(end=end, periods=n)
    data = {}
    for i, sigma in enumerate(sigmas):
        rets = 0.0003 + sigma * rng.standard_normal(n)
        data[SECTORS9[i]] = 100.0 * np.exp(np.cumsum(rets))
    return pd.DataFrame(data, index=index)


def _one_factor_closes(n: int = 400, seed: int = 7) -> pd.DataFrame:
    """Close frame where every name loads on one common factor, so all covariances are positive.

    XLE (second column) has a 0.1 beta to the factor and tiny idiosyncratic noise. Its
    equal-risk-contribution weight is large enough that the 25% cap binds on it.
    """
    rng = np.random.default_rng(seed)
    index = pd.bdate_range(end=MONTH_FIRST_END, periods=n)
    factor = rng.standard_normal(n) * 0.01
    betas = [1.0, 0.1, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    sds = [0.005, 0.0005] + [0.005] * 7
    data = {}
    for i, ticker in enumerate(SECTORS9):
        rets = 0.0003 + betas[i] * factor + sds[i] * rng.standard_normal(n)
        data[ticker] = 100.0 * np.exp(np.cumsum(rets))
    return pd.DataFrame(data, index=index)


def test_equal_vol_names_get_near_equal_risk_weights():
    closes = _closes([0.01] * 9, 400)
    weights = compute_weights(closes, LOOKBACK, CAP)
    assert weights is not None
    assert set(weights) == set(SECTORS9)
    for w in weights.values():
        assert abs(w - 1 / 9) < 0.02


def test_higher_vol_name_gets_smaller_weight_than_low_vol_name():
    # XLB is 4x as volatile as XLE; all other names share a middle vol.
    sigmas = [0.04, 0.01] + [0.015] * 7
    closes = _closes(sigmas, 400)
    weights = compute_weights(closes, LOOKBACK, 1.0)
    assert weights is not None
    assert weights["XLB"] < weights["XLE"]


def test_weights_sum_to_one_and_respect_cap():
    # XLE is very low vol with positive covariance to the rest, so ERC pushes it above the cap.
    closes = _one_factor_closes()
    weights = compute_weights(closes, LOOKBACK, CAP)
    assert weights is not None
    assert abs(sum(weights.values()) - 1.0) < 1e-6
    for w in weights.values():
        assert w <= CAP + 1e-9
    assert abs(weights["XLE"] - CAP) < 1e-9


def test_mid_month_bar_returns_none():
    closes = _closes([0.01] * 9, 400, end=MID_MONTH_END)
    assert compute_weights(closes, LOOKBACK, CAP) is None


def test_too_short_history_returns_empty():
    closes = _closes([0.01] * 9, 10)
    assert compute_weights(closes, LOOKBACK, CAP) == {}


def test_constant_prices_return_empty():
    index = pd.bdate_range(end=MONTH_FIRST_END, periods=300)
    closes = pd.DataFrame(dict.fromkeys(SECTORS9, 50.0), index=index)
    assert compute_weights(closes, LOOKBACK, CAP) == {}


def test_generic_weights_are_long_only_and_bounded_on_random_walk():
    closes = _closes([0.01 + 0.002 * i for i in range(9)], 600)
    weights = compute_weights(closes, LOOKBACK, CAP)
    assert weights is not None
    assert all(w >= 0.0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights
    assert set(weights) <= set(SECTORS9)


def test_bot_class_exposes_param_grid_and_backtest_period():
    assert QuantsmdRiskParityCappedBot.BACKTEST_PERIOD == "max"
    assert QuantsmdRiskParityCappedBot.param_grid == {
        "lookback": [63, 126, 252],
        "cap": [0.20, 0.25, 0.30],
    }
    assert list(SECTORS9) == UNIVERSE
