import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_intraday_rsi_15m import (
    CAP,
    ENTRY,
    EXIT,
    RSI_PERIOD,
    UNIVERSE,
    QuantsmdIntradayRsi15mBot,
    compute_weights,
)
from tradingbot.utils.quantsmd_common import rsi, rsi_hysteresis

START = "2021-03-01 09:30"


def _frame(columns: dict[str, np.ndarray]) -> pd.DataFrame:
    n = len(next(iter(columns.values())))
    index = pd.date_range(START, periods=n, freq="15min")
    return pd.DataFrame(columns, index=index)


def _rising(n: int) -> np.ndarray:
    return 100.0 + 0.5 * np.arange(n)


def _falling(n: int) -> np.ndarray:
    return 200.0 - 0.5 * np.arange(n)


def _fall_then_rise(fall: int, rise: int) -> np.ndarray:
    down = 200.0 - 0.5 * np.arange(fall)
    up = down[-1] + 0.5 * np.arange(1, rise + 1)
    return np.concatenate([down, up])


def _compute(closes: pd.DataFrame) -> dict[str, float]:
    return compute_weights(closes, RSI_PERIOD, ENTRY, EXIT, CAP)


def test_fewer_than_20_rows_returns_empty():
    closes = _frame({t: _falling(19) for t in UNIVERSE})
    assert _compute(closes) == {}


def test_falling_prices_held_at_cap():
    closes = _frame({t: _falling(60) for t in UNIVERSE})
    weights = _compute(closes)
    assert set(weights) == set(UNIVERSE)
    for w in weights.values():
        assert w == pytest.approx(CAP)
    assert sum(weights.values()) == pytest.approx(0.75)


def test_rising_prices_return_empty():
    closes = _frame({t: _rising(60) for t in UNIVERSE})
    assert _compute(closes) == {}


def test_only_the_falling_name_is_held():
    cols = {t: _rising(60) for t in UNIVERSE}
    cols[UNIVERSE[0]] = _falling(60)
    weights = _compute(_frame(cols))
    assert weights == {UNIVERSE[0]: pytest.approx(CAP)}


def test_exit_above_55_goes_flat_after_recovery():
    # RSI drops below 35 during the fall, then rises well above 55 on the rebound.
    closes = _frame({t: _fall_then_rise(30, 60) for t in UNIVERSE})
    assert _compute(closes) == {}


def test_weights_match_hysteresis_helper_on_random_walk():
    rng = np.random.default_rng(11)
    n = 300
    cols = {}
    for t in UNIVERSE:
        steps = rng.normal(0.0, 0.004, n)
        cols[t] = 100.0 * np.exp(np.cumsum(steps))
    closes = _frame(cols)
    expected = [t for t in UNIVERSE if rsi_hysteresis(rsi(closes[t], RSI_PERIOD), ENTRY, EXIT)]
    weights = _compute(closes)
    assert set(weights) == set(expected)
    for w in weights.values():
        assert w == pytest.approx(min(1.0 / len(expected), CAP))


def test_generic_weights_nonnegative_and_bounded():
    rng = np.random.default_rng(7)
    n = 600
    cols = {}
    for t in UNIVERSE:
        steps = rng.normal(0.0, 0.004, n)
        cols[t] = 100.0 * np.exp(np.cumsum(steps))
    weights = _compute(_frame(cols))
    assert all(w >= 0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights
    assert set(weights) <= set(UNIVERSE)


def test_generic_too_short_history_never_raises():
    rng = np.random.default_rng(3)
    closes = _frame({t: 100.0 + rng.normal(0.0, 1.0, 10) for t in UNIVERSE})
    assert _compute(closes) == {}


def test_generic_bot_class_metadata_readable():
    assert QuantsmdIntradayRsi15mBot.param_grid == {
        "entry": [30, 35, 40],
        "exit": [50, 55, 60],
    }
    assert QuantsmdIntradayRsi15mBot.BACKTEST_PERIOD == "60d"
