import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_weekly_rsi_reversion import (
    CAP,
    ENTRY,
    EXIT,
    RSI_PERIOD,
    UNIVERSE,
    QuantsmdWeeklyRsiReversionBot,
    compute_weights,
)

FRIDAY_END = "2024-01-05"  # a known Friday
THURSDAY_END = "2024-01-04"  # the day before


def _friday_index(periods: int) -> pd.DatetimeIndex:
    return pd.bdate_range(end=FRIDAY_END, periods=periods)


def _frame(columns: dict[str, np.ndarray], index: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(columns, index=index)


def _rising(n: int, start: float = 100.0) -> np.ndarray:
    return start + np.arange(n) * 0.5


def _sell_off_path() -> np.ndarray:
    """Rise 40 bars, fall 12 bars (RSI well below 35), then a bounce to ~43 RSI."""
    up = _rising(40)
    down = up[-1] - np.arange(1, 13) * 1.5
    bounce = down[-1] + np.arange(1, 9) * 0.6
    return np.concatenate([up, down, bounce])


def _sell_off_end_path() -> np.ndarray:
    """Rise 48 bars then fall 12 bars, so RSI sits well below 35 on the last bar."""
    up = _rising(48)
    down = up[-1] - np.arange(1, 13) * 1.5
    return np.concatenate([up, down])


def _compute(closes: pd.DataFrame) -> dict[str, float] | None:
    return compute_weights(closes, RSI_PERIOD, ENTRY, EXIT, CAP)


def test_non_friday_last_bar_returns_none():
    index = pd.bdate_range(end=THURSDAY_END, periods=60)
    closes = _frame({t: _rising(60) for t in UNIVERSE}, index)
    assert _compute(closes) is None


def test_friday_sell_off_is_held_at_cap():
    index = _friday_index(60)
    cols = {t: _rising(60) for t in UNIVERSE}
    cols[UNIVERSE[0]] = _sell_off_end_path()
    weights = _compute(_frame(cols, index))
    assert weights is not None
    assert UNIVERSE[0] in weights
    assert weights[UNIVERSE[0]] <= CAP + 1e-9
    assert weights[UNIVERSE[0]] == pytest.approx(CAP)


def test_hysteresis_holds_after_dip_while_rsi_between_entry_and_exit():
    # RSI fell below 35 about ten bars ago and ends near 43, inside the 35..60 band.
    index = _friday_index(60)
    cols = {t: _rising(60) for t in UNIVERSE}
    cols[UNIVERSE[0]] = _sell_off_path()
    weights = _compute(_frame(cols, index))
    assert weights is not None
    assert UNIVERSE[0] in weights
    assert weights[UNIVERSE[0]] == pytest.approx(CAP)


def test_rising_prices_return_empty():
    index = _friday_index(300)
    closes = _frame({t: _rising(300) for t in UNIVERSE}, index)
    assert _compute(closes) == {}


def test_fewer_than_warmup_rows_return_empty():
    index = _friday_index(RSI_PERIOD + 1)
    closes = _frame({t: _rising(RSI_PERIOD + 1) for t in UNIVERSE}, index)
    assert _compute(closes) == {}


def test_generic_weights_nonnegative_and_bounded():
    rng = np.random.default_rng(7)
    n = 600
    index = _friday_index(n)
    cols = {}
    for t in UNIVERSE:
        steps = rng.normal(0.0, 0.01, n)
        cols[t] = 100.0 * np.exp(np.cumsum(steps))
    weights = _compute(_frame(cols, index))
    assert weights is not None
    assert all(w >= 0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights


def test_generic_too_short_history_never_raises():
    rng = np.random.default_rng(3)
    index = _friday_index(10)
    closes = _frame({t: 100.0 + rng.normal(0.0, 1.0, 10) for t in UNIVERSE}, index)
    assert _compute(closes) == {}


def test_generic_bot_class_metadata_readable():
    assert QuantsmdWeeklyRsiReversionBot.param_grid == {
        "entry": [30, 35, 40],
        "exit": [55, 60, 65],
    }
    assert QuantsmdWeeklyRsiReversionBot.BACKTEST_PERIOD == "max"
