import numpy as np
import pandas as pd

from tradingbot.quantsmd_fx_etf_time_series_momentum import (
    FXETF6,
    QuantsmdFxEtfTimeSeriesMomentumBot,
    compute_weights,
)

# 2024-08-01 is a Thursday and the bar before it (2024-07-31) is in July, so a
# frame ending here has a month-first last bar.
MONTH_FIRST_END = "2024-08-01"
# 2024-07-15 is mid-month, so a frame ending here is not month-first.
MID_MONTH_END = "2024-07-15"


def _index(end: str, periods: int) -> pd.DatetimeIndex:
    return pd.bdate_range(end=end, periods=periods)


def _drift_frame(
    end: str,
    drifts: list[float],
    periods: int = 300,
    seed: int = 3,
    noise_scales: list[float] | None = None,
) -> pd.DataFrame:
    """Each column has its own drift; noise is shared unless per-column scales are given."""
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, 0.01, size=periods)
    scales = noise_scales or [1.0] * len(drifts)
    returns = np.column_stack([d + s * noise for d, s in zip(drifts, scales, strict=True)])
    closes = 100.0 * np.exp(np.cumsum(returns, axis=0))
    return pd.DataFrame(closes, index=_index(end, periods), columns=FXETF6)


def test_non_month_start_bar_returns_none():
    closes = _drift_frame(MID_MONTH_END, [0.003] * 6)
    assert compute_weights(closes, lookback=126) is None


def test_short_history_returns_empty_book():
    closes = _drift_frame(MONTH_FIRST_END, [0.003] * 6, periods=100)
    assert compute_weights(closes, lookback=126) == {}


def test_two_rising_and_four_falling_holds_only_the_rising_two():
    drifts = [0.003, 0.003, -0.003, -0.003, -0.003, -0.003]
    closes = _drift_frame(MONTH_FIRST_END, drifts)
    weights = compute_weights(closes, lookback=126)
    assert weights is not None
    assert set(weights) == {"FXE", "FXB"}
    assert all(w > 0.0 for w in weights.values())
    assert abs(sum(weights.values()) - 1.0) < 1e-9
    assert "USD" not in weights


def test_inverse_vol_gives_calmer_rising_etf_the_larger_weight():
    drifts = [0.003, 0.003, -0.003, -0.003, -0.003, -0.003]
    scales = [0.5, 2.0, 1.0, 1.0, 1.0, 1.0]
    closes = _drift_frame(MONTH_FIRST_END, drifts, noise_scales=scales)
    weights = compute_weights(closes, lookback=126)
    assert weights is not None
    assert set(weights) == {"FXE", "FXB"}
    # FXE has the smaller noise, so its inverse vol and therefore its weight is larger.
    assert weights["FXE"] > weights["FXB"]
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_all_falling_etfs_give_empty_book():
    closes = _drift_frame(MONTH_FIRST_END, [-0.003] * 6)
    assert compute_weights(closes, lookback=126) == {}


def test_bot_class_metadata_is_readable():
    assert QuantsmdFxEtfTimeSeriesMomentumBot.param_grid["lookback"] == [63, 126, 252]
    assert QuantsmdFxEtfTimeSeriesMomentumBot.BACKTEST_PERIOD == "max"
    assert FXETF6 == ["FXE", "FXB", "FXA", "FXY", "FXC", "FXF"]
