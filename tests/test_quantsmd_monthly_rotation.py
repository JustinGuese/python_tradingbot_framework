import numpy as np
import pandas as pd

from tradingbot.quantsmd_monthly_rotation import (
    QuantsmdMonthlyRotationBot,
    compute_weights,
)
from tradingbot.utils.quantsmd_common import ETF8

# 2024-08-01 is a Thursday and the bar before it (2024-07-31) is in July, so a
# frame ending here has a month-first last bar.
MONTH_FIRST_END = "2024-08-01"
# 2024-07-15 is mid-month, so a frame ending here is not month-first.
MID_MONTH_END = "2024-07-15"


def _index(end: str, periods: int) -> pd.DatetimeIndex:
    return pd.bdate_range(end=end, periods=periods)


def _random_walk(end: str, periods: int, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = _index(end, periods)
    returns = rng.normal(0.0004, 0.01, size=(periods, len(ETF8)))
    closes = 100.0 * np.exp(np.cumsum(returns, axis=0))
    return pd.DataFrame(closes, index=idx, columns=ETF8)


def test_weights_sum_to_one_even_when_every_etf_is_falling():
    idx = _index(MONTH_FIRST_END, 300)
    # Every ETF falls, each at a slightly different rate, so the ranking is well defined.
    slopes = -0.0005 - 0.0002 * np.arange(len(ETF8))
    closes = pd.DataFrame(
        100.0 * np.exp(np.outer(np.arange(300), slopes)),
        index=idx,
        columns=ETF8,
    )
    weights = compute_weights(closes, lookback=126, top_n=3)
    assert weights is not None
    assert len(weights) == 3
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_exactly_top_n_highest_momentum_names_are_held():
    idx = _index(MONTH_FIRST_END, 300)
    # Deterministic exponential growth, increasing with column position, so
    # the last three columns have the highest trailing momentum.
    rates = 0.0002 + 0.0004 * np.arange(len(ETF8))
    closes = pd.DataFrame(
        100.0 * np.exp(np.outer(np.arange(300), rates)),
        index=idx,
        columns=ETF8,
    )
    weights = compute_weights(closes, lookback=126, top_n=3)
    assert weights is not None
    assert set(weights) == set(ETF8[-3:])
    assert len(weights) == 3
    for w in weights.values():
        assert abs(w - 1.0 / 3.0) < 1e-12


def test_mid_month_bar_returns_none():
    closes = _random_walk(MID_MONTH_END, 300)
    assert compute_weights(closes, lookback=126, top_n=3) is None


def test_fewer_valid_tickers_than_top_n_split_equally_among_available():
    idx = _index(MONTH_FIRST_END, 300)
    closes = pd.DataFrame(index=idx, columns=ETF8, dtype=float)
    closes["SPY"] = 100.0 * np.exp(np.arange(300) * 0.001)
    closes["TLT"] = 100.0 * np.exp(np.arange(300) * 0.0005)
    # All other tickers have no data on the last bar, so they cannot be ranked.
    for t in ETF8:
        if t not in ("SPY", "TLT"):
            closes[t] = np.nan
    weights = compute_weights(closes, lookback=126, top_n=3)
    assert weights == {"SPY": 0.5, "TLT": 0.5}


# --- Generic cases (G1-G3) ---


def test_generic_weights_are_nonnegative_and_sum_at_most_one():
    closes = _random_walk(MONTH_FIRST_END, 600, seed=7)
    weights = compute_weights(closes, lookback=126, top_n=3)
    assert weights is not None
    assert all(w >= 0.0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights


def test_generic_too_short_history_returns_empty_or_none():
    closes = _random_walk(MONTH_FIRST_END, 10)
    result = compute_weights(closes, lookback=126, top_n=3)
    assert result is None or result == {}


def test_generic_bot_class_metadata_is_readable():
    assert "lookback" in QuantsmdMonthlyRotationBot.param_grid
    assert "top_n" in QuantsmdMonthlyRotationBot.param_grid
    assert QuantsmdMonthlyRotationBot.BACKTEST_PERIOD == "max"
