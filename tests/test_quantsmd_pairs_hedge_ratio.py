import numpy as np
import pandas as pd

from tradingbot.quantsmd_pairs_hedge_ratio import (
    UNIVERSE,
    WINDOW,
    QuantsmdPairsHedgeRatioBot,
    compute_weights,
    pair_state,
)
from tradingbot.utils.botclass import Bot

A, B = UNIVERSE


def _frame(columns: dict[str, np.ndarray]) -> pd.DataFrame:
    n = len(next(iter(columns.values())))
    index = pd.bdate_range("2020-01-01", periods=n)
    return pd.DataFrame(columns, index=index)


def _pair(e: np.ndarray) -> pd.DataFrame:
    """Build EWA/EWC closes with log(A) = beta * log(B) + e, beta = 1.

    ``lb`` is a slow sinusoid around zero, so the rolling regression slope is
    close to 1 and the residual ``e`` drives the hedged spread.
    """
    n = len(e)
    lb = 0.1 * np.sin(np.arange(n) / 3.0)
    return _frame({A: np.exp(lb + e), B: np.exp(lb)})


def _calm(n: int) -> np.ndarray:
    """Residual alternating +/-0.001, so every rolling z-score stays inside +/-2."""
    return np.where(np.arange(n) % 2 == 0, 0.001, -0.001)


def test_pair_state_enters_long_a_below_minus_two_then_holds_inside_band():
    z = pd.Series([0.0, -2.5, -1.0, -0.6])
    assert pair_state(z) == 1


def test_pair_state_exits_to_flat_once_abs_z_below_half():
    z = pd.Series([0.0, -2.5, -1.0, -0.3])
    assert pair_state(z) == 0


def test_pair_state_enters_short_a_above_plus_two():
    z = pd.Series([0.0, 2.5, 1.0])
    assert pair_state(z) == -1


def test_pair_state_has_no_direct_flip_from_long_to_short():
    z = pd.Series([-2.5, 2.5])
    assert pair_state(z) == 1


def test_pair_state_nan_resets_to_flat():
    z = pd.Series([-2.5, np.nan])
    assert pair_state(z) == 0


def test_pair_state_inside_band_never_opens():
    z = pd.Series([0.4, -1.9, 1.9, 0.0])
    assert pair_state(z) == 0


def test_199_rows_returns_empty():
    closes = _pair(_calm(199))
    assert compute_weights(closes, A, B) == {}


def test_60_rows_returns_empty_below_minimum():
    e = _calm(60)
    e[-1] = -0.2
    closes = _pair(e)
    assert compute_weights(closes, A, B) == {}


def test_final_z_below_minus_two_holds_a_at_half():
    e = _calm(120)
    e[-1] = -0.2
    closes = _pair(e)
    assert compute_weights(closes, A, B) == {A: 0.5}


def test_final_z_above_plus_two_holds_b_at_half():
    e = _calm(120)
    e[-1] = 0.2
    closes = _pair(e)
    assert compute_weights(closes, A, B) == {B: 0.5}


def test_calm_spread_returns_empty():
    closes = _pair(_calm(120))
    assert compute_weights(closes, A, B) == {}


def test_generic_weights_nonnegative_and_bounded():
    rng = np.random.default_rng(7)
    n = 600
    cols = {}
    for t in UNIVERSE:
        steps = rng.normal(0.0002, 0.01, n)
        cols[t] = 100.0 * np.exp(np.cumsum(steps))
    closes = _frame(cols)
    weights = compute_weights(closes, A, B)
    assert all(w >= 0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights
    assert set(weights) <= set(UNIVERSE)


def test_generic_bot_class_metadata_readable():
    assert issubclass(QuantsmdPairsHedgeRatioBot, Bot)
    assert QuantsmdPairsHedgeRatioBot.__name__ == "QuantsmdPairsHedgeRatioBot"
    assert UNIVERSE == ["EWA", "EWC"]
    assert QuantsmdPairsHedgeRatioBot.param_grid == {"window": [40, 60, 120]}
    assert hasattr(QuantsmdPairsHedgeRatioBot, "BACKTEST_PERIOD")
    assert WINDOW == 60
