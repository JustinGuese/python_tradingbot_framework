"""Tests for the quants.md multi-asset risk-parity bot (pure compute_weights, synthetic data)."""

import numpy as np
import pandas as pd

from tradingbot.quantsmd_risk_parity_multiasset import (
    LOOKBACK,
    UNIVERSE,
    QuantsmdRiskParityMultiassetBot,
    compute_weights,
)
from tradingbot.utils.quantsmd_common import ETF8

MONTH_FIRST_END = "2024-02-01"  # Thursday; the previous bar is 2024-01-31, a new month.
MID_MONTH_END = "2024-01-17"


def _closes(sigmas: list[float], n: int, end: str = MONTH_FIRST_END, seed: int = 7) -> pd.DataFrame:
    """Business-day close frame, one column per sigma, independent daily noise."""
    rng = np.random.default_rng(seed)
    index = pd.bdate_range(end=end, periods=n)
    data = {}
    for i, sigma in enumerate(sigmas):
        rets = 0.0003 + sigma * rng.standard_normal(n)
        data[ETF8[i]] = 100.0 * np.exp(np.cumsum(rets))
    return pd.DataFrame(data, index=index)


def test_equal_vol_names_get_near_equal_risk_weights():
    closes = _closes([0.01] * 8, 400)
    weights = compute_weights(closes, LOOKBACK)
    assert weights is not None
    assert set(weights) == set(ETF8)
    # 63 noisy daily returns per name leave ~9% sampling error in each vol,
    # so equal-vol names land within a few points of 1/8, not exactly on it.
    for w in weights.values():
        assert abs(w - 1 / 8) < 0.03


def test_higher_vol_name_gets_smaller_weight_than_low_vol_name():
    # SPY is 4x as volatile as TLT; all other names share a middle vol.
    sigmas = [0.04, 0.01] + [0.015] * 6
    closes = _closes(sigmas, 400)
    weights = compute_weights(closes, LOOKBACK)
    assert weights is not None
    assert weights["SPY"] < weights["TLT"]


def test_two_assets_with_three_times_vol_ratio_get_inverse_vol_weights():
    rng = np.random.default_rng(3)
    index = pd.bdate_range(end=MONTH_FIRST_END, periods=200)
    low = 100.0 * np.exp(np.cumsum(0.01 * rng.standard_normal(200)))
    high = 100.0 * np.exp(np.cumsum(0.03 * rng.standard_normal(200)))
    closes = pd.DataFrame({"SPY": low, "TLT": high}, index=index)
    weights = compute_weights(closes, LOOKBACK)
    assert weights is not None
    assert abs(weights["SPY"] - 0.75) < 0.05
    assert abs(weights["TLT"] - 0.25) < 0.05
    assert abs(sum(weights.values()) - 1.0) < 1e-6


def test_mid_month_bar_returns_none():
    closes = _closes([0.01] * 8, 400, end=MID_MONTH_END)
    assert compute_weights(closes, LOOKBACK) is None


def test_too_short_history_returns_empty():
    closes = _closes([0.01] * 8, 10)
    assert compute_weights(closes, LOOKBACK) == {}


def test_63_rows_is_the_minimum_and_62_returns_empty():
    closes = _closes([0.01] * 8, 63)
    assert compute_weights(closes, LOOKBACK) == {}
    closes_ok = _closes([0.01] * 8, 64)
    assert compute_weights(closes_ok, LOOKBACK) is not None


def test_constant_prices_return_empty():
    index = pd.bdate_range(end=MONTH_FIRST_END, periods=300)
    closes = pd.DataFrame(dict.fromkeys(ETF8, 50.0), index=index)
    assert compute_weights(closes, LOOKBACK) == {}


def test_generic_weights_are_long_only_and_bounded_on_random_walk():
    closes = _closes([0.01 + 0.002 * i for i in range(8)], 600)
    weights = compute_weights(closes, LOOKBACK)
    assert weights is not None
    assert all(w >= 0.0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights
    assert set(weights) <= set(ETF8)


def test_bot_class_exposes_param_grid_and_backtest_period():
    assert QuantsmdRiskParityMultiassetBot.BACKTEST_PERIOD == "max"
    assert QuantsmdRiskParityMultiassetBot.param_grid == {"lookback": [63, 126]}
    assert list(ETF8) == UNIVERSE
