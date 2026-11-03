import numpy as np
import pandas as pd

from tradingbot.quantsmd_monthly_inverse_vol import (
    UNIVERSE,
    QuantsmdMonthlyInverseVolBot,
    compute_weights,
)

MONTH_FIRST_END = "2024-02-01"  # Thursday; previous bar 2024-01-31 is in January
MID_MONTH_END = "2024-01-31"  # previous bar 2024-01-30 is in the same month


def _index(end: str, periods: int) -> pd.DatetimeIndex:
    return pd.bdate_range(end=end, periods=periods)


def _closes_from_returns(returns: dict[str, np.ndarray], index: pd.DatetimeIndex) -> pd.DataFrame:
    frame = {}
    for ticker, rets in returns.items():
        frame[ticker] = 100.0 * np.cumprod(1.0 + rets)
    return pd.DataFrame(frame, index=index)


def _noisy(rng: np.random.Generator, n: int, sigma: float, drift: float = 0.0) -> np.ndarray:
    return drift + sigma * rng.standard_normal(n)


def test_one_low_vol_ticker_is_capped_and_sum_below_one():
    rng = np.random.default_rng(1)
    n = 120
    returns = {t: _noisy(rng, n, 0.02) for t in UNIVERSE}
    returns["SPY"] = _noisy(rng, n, 0.0005)  # very low volatility
    closes = _closes_from_returns(returns, _index(MONTH_FIRST_END, n))
    weights = compute_weights(closes, lookback=63, cap=0.35)
    assert weights is not None
    assert weights["SPY"] == 0.35
    assert sum(weights.values()) < 1.0
    assert all(w <= 0.35 + 1e-12 for w in weights.values())


def test_equal_vol_tickers_get_equal_weights():
    rng = np.random.default_rng(2)
    n = 120
    shared = _noisy(rng, n, 0.01)
    returns = {t: shared.copy() for t in UNIVERSE}  # identical vol for all eight
    closes = _closes_from_returns(returns, _index(MONTH_FIRST_END, n))
    weights = compute_weights(closes, lookback=63, cap=0.35)
    assert weights is not None
    assert set(weights) == set(UNIVERSE)
    for w in weights.values():
        assert abs(w - 0.125) < 1e-9


def test_mid_month_bar_returns_none():
    rng = np.random.default_rng(3)
    n = 120
    returns = {t: _noisy(rng, n, 0.01) for t in UNIVERSE}
    closes = _closes_from_returns(returns, _index(MID_MONTH_END, n))
    assert compute_weights(closes, lookback=63, cap=0.35) is None


def test_constant_price_ticker_is_omitted():
    rng = np.random.default_rng(4)
    n = 120
    returns = {t: _noisy(rng, n, 0.01) for t in UNIVERSE}
    returns["TLT"] = np.zeros(n)  # flat price, zero volatility
    closes = _closes_from_returns(returns, _index(MONTH_FIRST_END, n))
    weights = compute_weights(closes, lookback=63, cap=0.35)
    assert weights is not None
    assert "TLT" not in weights
    assert set(weights) == set(UNIVERSE) - {"TLT"}


def test_generic_weights_are_nonnegative_and_bounded_on_random_walk():
    rng = np.random.default_rng(5)
    n = 600
    returns = {t: _noisy(rng, n, 0.01 + 0.002 * i) for i, t in enumerate(UNIVERSE)}
    closes = _closes_from_returns(returns, _index(MONTH_FIRST_END, n))
    weights = compute_weights(closes, lookback=63, cap=0.35)
    assert weights is not None
    assert all(w >= 0.0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights


def test_generic_too_short_history_returns_empty_or_none():
    rng = np.random.default_rng(6)
    n = 10
    returns = {t: _noisy(rng, n, 0.01) for t in UNIVERSE}
    closes = _closes_from_returns(returns, _index(MONTH_FIRST_END, n))
    result = compute_weights(closes, lookback=63, cap=0.35)
    assert result is None or result == {}


def test_generic_class_attributes_readable():
    assert QuantsmdMonthlyInverseVolBot.BACKTEST_PERIOD == "max"
    assert set(QuantsmdMonthlyInverseVolBot.param_grid) == {"lookback", "cap"}
    assert QuantsmdMonthlyInverseVolBot.param_grid["lookback"] == [42, 63, 126]
    assert QuantsmdMonthlyInverseVolBot.param_grid["cap"] == [0.30, 0.35, 0.40]
