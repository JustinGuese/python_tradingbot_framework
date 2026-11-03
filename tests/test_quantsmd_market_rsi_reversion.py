import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_market_rsi_reversion import (
    ENTRY,
    EXIT,
    FRACTION,
    RSI_PERIOD,
    UNIVERSE,
    QuantsmdMarketRsiReversionBot,
    compute_weights,
)


def _frame(columns: dict[str, np.ndarray]) -> pd.DataFrame:
    n = len(next(iter(columns.values())))
    index = pd.bdate_range("2020-01-01", periods=n)
    return pd.DataFrame(columns, index=index)


def _rising(n: int, start: float = 100.0) -> np.ndarray:
    return start + np.arange(n) * 0.5


def _sell_off_end_path() -> np.ndarray:
    """Rise 48 bars then fall 12 bars, so RSI sits well below 35 on the last bar."""
    up = _rising(48)
    down = up[-1] - np.arange(1, 13) * 1.5
    return np.concatenate([up, down])


def _sell_off_bounce_path() -> np.ndarray:
    """Rise 40 bars, fall 12 bars (RSI below 35), then bounce to RSI ~43, inside 35..60."""
    up = _rising(40)
    down = up[-1] - np.arange(1, 13) * 1.5
    bounce = down[-1] + np.arange(1, 9) * 0.6
    return np.concatenate([up, down, bounce])


def _sell_off_then_rally_path() -> np.ndarray:
    """Sell-off that enters the hold, then a rally that drives RSI above 60 (exit)."""
    down_leg = _sell_off_end_path()
    rally = down_leg[-1] + np.arange(1, 41) * 1.5
    return np.concatenate([down_leg, rally])


def test_all_rising_returns_empty():
    n = 300
    closes = _frame({t: _rising(n) for t in UNIVERSE})
    assert compute_weights(closes) == {}


def test_sell_off_on_last_bar_holds_at_fraction():
    n = 60
    cols = {t: _rising(n) for t in UNIVERSE}
    cols[UNIVERSE[0]] = _sell_off_end_path()
    weights = compute_weights(_frame(cols))
    assert set(weights) == {UNIVERSE[0]}
    assert weights[UNIVERSE[0]] == pytest.approx(FRACTION)


def test_hysteresis_holds_while_rsi_between_entry_and_exit():
    # RSI dipped below 35 about ten bars ago and ends near 43, inside the 35..60 band.
    n = 60
    cols = {t: _rising(n) for t in UNIVERSE}
    cols[UNIVERSE[0]] = _sell_off_bounce_path()
    weights = compute_weights(_frame(cols))
    assert weights == {UNIVERSE[0]: pytest.approx(FRACTION)}


def test_rally_above_exit_flattens():
    # E3: the exit fires, RSI rises above 60 after the entry, and the book is empty.
    path = _sell_off_then_rally_path()
    cols = {t: _rising(len(path)) for t in UNIVERSE}
    cols[UNIVERSE[0]] = path
    assert compute_weights(_frame(cols)) == {}


def test_short_history_returns_empty():
    # T1: fewer rows than the RSI warmup gives no held flag.
    closes = _frame({t: _rising(RSI_PERIOD) for t in UNIVERSE})
    assert compute_weights(closes) == {}


def test_empty_frame_returns_empty():
    assert compute_weights(pd.DataFrame()) == {}


def test_generic_weights_nonnegative_and_bounded():
    rng = np.random.default_rng(7)
    n = 600
    cols = {}
    for t in UNIVERSE:
        steps = rng.normal(0.0002, 0.01, n)
        cols[t] = 100.0 * np.exp(np.cumsum(steps))
    weights = compute_weights(_frame(cols))
    assert all(w >= 0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights
    assert set(weights) <= set(UNIVERSE)


def test_generic_bot_class_metadata_readable():
    assert QuantsmdMarketRsiReversionBot.__name__ == "QuantsmdMarketRsiReversionBot"
    assert QuantsmdMarketRsiReversionBot.param_grid == {
        "entry": [30, 35, 40],
        "exit": [55, 60, 65],
    }
    assert QuantsmdMarketRsiReversionBot.BACKTEST_PERIOD == "max"
    assert ENTRY == 35.0
    assert EXIT == 60.0
