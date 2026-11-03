import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_quarterly_dual_momentum import (
    CAP,
    LOOKBACK,
    TOP_N,
    UNIVERSE,
    QuantsmdQuarterlyDualMomentumBot,
    compute_weights,
)

# 2021-04-01 is the first Thursday of Q2 (a quarter start); 2021-05-03 is mid-quarter.
QUARTER_START = "2021-04-01"
MID_QUARTER = "2021-05-03"


def _frame(columns: dict[str, np.ndarray], end: str = QUARTER_START) -> pd.DataFrame:
    n = len(next(iter(columns.values())))
    index = pd.bdate_range(end=end, periods=n)
    return pd.DataFrame(columns, index=index)


def _up(n: int, drift: float = 0.001) -> np.ndarray:
    return 100.0 * np.exp(drift * np.arange(n))


def _down(n: int, drift: float = 0.001) -> np.ndarray:
    return 100.0 * np.exp(-drift * np.arange(n))


def test_off_quarter_bar_returns_none():
    closes = _frame({t: _up(300) for t in UNIVERSE}, end=MID_QUARTER)
    assert compute_weights(closes, LOOKBACK, TOP_N, CAP) is None


def test_252_rows_returns_empty_on_quarter_start():
    closes = _frame({t: _up(252) for t in UNIVERSE})
    assert compute_weights(closes, LOOKBACK, TOP_N, CAP) == {}


def test_top_two_positive_names_at_half_each():
    n = 300
    cols = {t: _down(n) for t in UNIVERSE}
    cols[UNIVERSE[0]] = _up(n, 0.003)
    cols[UNIVERSE[1]] = _up(n, 0.002)
    cols[UNIVERSE[2]] = _up(n, 0.001)
    closes = _frame(cols)
    weights = compute_weights(closes, LOOKBACK, TOP_N, CAP)
    assert set(weights) == {UNIVERSE[0], UNIVERSE[1]}
    for w in weights.values():
        assert w == pytest.approx(0.5)
    assert sum(weights.values()) == pytest.approx(1.0)


def test_only_one_positive_asset_gets_cap():
    n = 300
    cols = {t: _down(n) for t in UNIVERSE}
    cols[UNIVERSE[3]] = _up(n)
    closes = _frame(cols)
    weights = compute_weights(closes, LOOKBACK, TOP_N, CAP)
    assert set(weights) == {UNIVERSE[3]}
    assert weights[UNIVERSE[3]] == pytest.approx(0.5)


def test_no_positive_momentum_returns_empty():
    closes = _frame({t: _down(300) for t in UNIVERSE})
    assert compute_weights(closes, LOOKBACK, TOP_N, CAP) == {}


def test_generic_weights_nonnegative_and_bounded():
    rng = np.random.default_rng(11)
    n = 600
    cols = {}
    for t in UNIVERSE:
        steps = rng.normal(0.0002, 0.01, n)
        cols[t] = 100.0 * np.exp(np.cumsum(steps))
    closes = _frame(cols)
    weights = compute_weights(closes, LOOKBACK, TOP_N, CAP)
    assert all(w >= 0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights
    assert set(weights) <= set(UNIVERSE)


def test_generic_too_short_history_never_raises():
    closes = _frame({t: _up(10) for t in UNIVERSE})
    assert compute_weights(closes, LOOKBACK, TOP_N, CAP) == {}


def test_generic_bot_class_metadata_readable():
    assert QuantsmdQuarterlyDualMomentumBot.param_grid == {
        "lookback": [126, 252],
        "top_n": [2, 3],
    }
    assert hasattr(QuantsmdQuarterlyDualMomentumBot, "BACKTEST_PERIOD")
