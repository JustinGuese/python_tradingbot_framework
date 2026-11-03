import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_bollinger_reversion import (
    CAP,
    N_STD,
    UNIVERSE,
    WINDOW,
    QuantsmdBollingerReversionBot,
    compute_weights,
)


def _frame(columns: dict[str, np.ndarray]) -> pd.DataFrame:
    n = len(next(iter(columns.values())))
    index = pd.bdate_range("2020-01-01", periods=n)
    return pd.DataFrame(columns, index=index)


def _flat_then(tail: list[float], flat: int = 30) -> np.ndarray:
    return np.concatenate([np.full(flat, 100.0), np.array(tail, dtype=float)])


def _compute(closes: pd.DataFrame) -> dict[str, float]:
    return compute_weights(closes, WINDOW, N_STD, CAP)


def test_dip_below_lower_band_on_last_bar_is_held_at_cap():
    # Flat at 100, then a sharp drop on the final bar: mean ~99, lower ~90.
    cols = {t: np.full(31, 100.0) for t in UNIVERSE}
    cols[UNIVERSE[0]] = _flat_then([80.0], flat=30)
    weights = _compute(_frame(cols))
    assert set(weights) == {UNIVERSE[0]}
    assert weights[UNIVERSE[0]] == pytest.approx(CAP)


def test_hysteresis_holds_while_not_yet_above_mean():
    # Dip three bars ago, partial recovery, still below the rolling mean.
    cols = {t: np.full(33, 100.0) for t in UNIVERSE}
    cols[UNIVERSE[0]] = _flat_then([80.0, 82.0, 85.0], flat=30)
    weights = _compute(_frame(cols))
    assert UNIVERSE[0] in weights
    assert weights[UNIVERSE[0]] == pytest.approx(CAP)


def test_recovery_above_mean_exits():
    # Same dip, then a jump to 110 which is above the rolling mean (~97.85).
    cols = {t: np.full(34, 100.0) for t in UNIVERSE}
    cols[UNIVERSE[0]] = _flat_then([80.0, 82.0, 85.0, 110.0], flat=30)
    weights = _compute(_frame(cols))
    assert UNIVERSE[0] not in weights


def test_flat_prices_return_empty():
    closes = _frame({t: np.full(300, 100.0) for t in UNIVERSE})
    assert _compute(closes) == {}


def test_ten_rows_return_empty():
    closes = _frame({t: np.full(10, 100.0) for t in UNIVERSE})
    assert _compute(closes) == {}


def test_generic_weights_nonnegative_and_bounded():
    rng = np.random.default_rng(7)
    n = 600
    cols = {}
    for t in UNIVERSE:
        steps = rng.normal(0.0, 0.01, n)
        cols[t] = 100.0 * np.exp(np.cumsum(steps))
    weights = _compute(_frame(cols))
    assert all(w >= 0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights


def test_generic_too_short_history_never_raises():
    rng = np.random.default_rng(3)
    closes = _frame({t: 100.0 + rng.normal(0.0, 1.0, 10) for t in UNIVERSE})
    assert _compute(closes) == {}


def test_generic_bot_class_metadata_readable():
    assert QuantsmdBollingerReversionBot.param_grid == {
        "window": [10, 20, 30],
        "n_std": [1.5, 2.0, 2.5],
    }
    assert QuantsmdBollingerReversionBot.BACKTEST_PERIOD == "max"
