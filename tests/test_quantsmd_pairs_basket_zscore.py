import numpy as np
import pandas as pd

from tradingbot.quantsmd_pairs_basket_zscore import (
    PAIRS,
    UNIVERSE,
    QuantsmdPairsBasketZscoreBot,
    compute_weights,
)
from tradingbot.utils.botclass import Bot

SHARE = 1.0 / len(PAIRS)


def _frame(columns: dict[str, np.ndarray]) -> pd.DataFrame:
    n = len(next(iter(columns.values())))
    index = pd.bdate_range("2020-01-01", periods=n)
    return pd.DataFrame(columns, index=index)


def _calm(n: int) -> np.ndarray:
    """Spread alternating +/-0.001, so every rolling z-score stays inside +/-2."""
    return np.where(np.arange(n) % 2 == 0, 0.001, -0.001)


def _basket(spreads: dict[int, np.ndarray], n: int = 120) -> pd.DataFrame:
    """Build closes for all pairs. Pair ``i`` has log(a) - log(b) equal to its spread.

    Pairs not listed in ``spreads`` get a calm spread. The second leg of every
    pair is held at price 1, so its log is 0.
    """
    cols: dict[str, np.ndarray] = {}
    for i, (a, b) in enumerate(PAIRS):
        spread = spreads[i] if i in spreads else _calm(n)
        cols[a] = np.exp(spread)
        cols[b] = np.ones(n)
    return _frame(cols)


def _triggered_low(n: int = 120) -> np.ndarray:
    """Calm spread whose last bar is far below its mean, so z < -2 (a is cheap)."""
    spread = _calm(n)
    spread[-1] = -0.2
    return spread


def _triggered_high(n: int = 120) -> np.ndarray:
    """Calm spread whose last bar is far above its mean, so z > 2 (b is cheap)."""
    spread = _calm(n)
    spread[-1] = 0.2
    return spread


def test_one_pair_triggered_weights_only_its_cheap_leg_at_one_eighth():
    closes = _basket({0: _triggered_low()})
    assert compute_weights(closes) == {"KO": SHARE}
    assert SHARE == 0.125


def test_no_pair_triggered_returns_empty():
    closes = _basket({})
    assert compute_weights(closes) == {}


def test_two_pairs_triggered_hold_both_cheap_legs():
    closes = _basket({0: _triggered_low(), 1: _triggered_high()})
    assert compute_weights(closes) == {"KO": SHARE, "CVX": SHARE}


def test_short_history_returns_empty():
    closes = _basket({0: _triggered_low(60)}, n=60)
    assert compute_weights(closes) == {}


def test_generic_weights_nonnegative_bounded_and_on_universe():
    rng = np.random.default_rng(7)
    n = 600
    cols = {}
    for t in UNIVERSE:
        steps = rng.normal(0.0002, 0.01, n)
        cols[t] = 100.0 * np.exp(np.cumsum(steps))
    closes = _frame(cols)
    weights = compute_weights(closes)
    assert all(w >= 0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights
    assert set(weights) <= set(UNIVERSE)


def test_all_pairs_triggered_sum_to_at_most_one():
    spreads = {i: (_triggered_low() if i % 2 == 0 else _triggered_high()) for i in range(len(PAIRS))}
    closes = _basket(spreads)
    weights = compute_weights(closes)
    assert len(weights) == len(PAIRS)
    assert sum(weights.values()) <= 1.0 + 1e-9


def test_bot_class_metadata_readable():
    assert issubclass(QuantsmdPairsBasketZscoreBot, Bot)
    assert QuantsmdPairsBasketZscoreBot.__name__ == "QuantsmdPairsBasketZscoreBot"
    assert len(PAIRS) == 8
    assert len(UNIVERSE) == 16
    assert len(set(UNIVERSE)) == 16
    assert PAIRS[0] == ("KO", "PEP")
    assert QuantsmdPairsBasketZscoreBot.param_grid == {"window": [40, 60, 120]}
    assert QuantsmdPairsBasketZscoreBot.BACKTEST_PERIOD == "max"
