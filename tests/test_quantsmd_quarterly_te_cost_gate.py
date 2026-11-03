import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_quarterly_te_cost_gate import (
    CAP,
    LOOKBACK,
    MAX_ANNUAL_TURNOVER,
    TE_THRESHOLD,
    TE_WINDOW,
    TOP_N,
    UNIVERSE,
    QuantsmdQuarterlyTeCostGateBot,
    compute_weights,
)


def _frame(columns: dict[str, np.ndarray], end: str) -> pd.DataFrame:
    n = len(next(iter(columns.values())))
    index = pd.bdate_range(end=end, periods=n)
    return pd.DataFrame(columns, index=index)


def _path(n: int, drift: float, vol: float = 0.0, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    steps = drift + vol * rng.standard_normal(n)
    return 100.0 * np.exp(np.cumsum(steps))


def _fresh_state() -> dict:
    return {"last_target": None, "turnover": []}


def _call(closes: pd.DataFrame, state: dict) -> dict | None:
    return compute_weights(
        closes,
        LOOKBACK,
        TOP_N,
        CAP,
        TE_THRESHOLD,
        TE_WINDOW,
        MAX_ANNUAL_TURNOVER,
        state,
    )


def _three_up_five_down(end: str, n: int = 300) -> pd.DataFrame:
    cols = {}
    for i, t in enumerate(UNIVERSE):
        drift = 0.003 - 0.001 * i if i < 3 else -0.002
        cols[t] = 100.0 * np.exp(drift * np.arange(n))
    return _frame(cols, end)


def test_first_call_builds_book_with_top_three_equal_weight():
    closes = _three_up_five_down("2024-02-14")
    state = _fresh_state()
    weights = _call(closes, state)
    assert weights is not None
    assert set(weights) == set(UNIVERSE[:3])
    for w in weights.values():
        assert w == pytest.approx(1 / 3)
    assert state["last_target"] == weights


def test_mid_quarter_tracking_match_returns_none():
    closes = _three_up_five_down("2024-02-14")
    # Holding 100% SPY and benchmarking against SPY gives zero tracking error.
    state = {"last_target": {"SPY": 1.0}, "turnover": []}
    assert _call(closes, state) is None


def test_quarter_first_bar_rebalances():
    closes = _three_up_five_down("2024-04-01")
    state = {"last_target": {"SPY": 1.0}, "turnover": []}
    weights = _call(closes, state)
    assert isinstance(weights, dict)
    assert set(weights) == set(UNIVERSE[:3])


def test_turnover_budget_exhausted_blocks_rebalance():
    closes = _three_up_five_down("2024-04-01")
    index = closes.index
    state = {
        "last_target": {"TLT": 1.0},
        "turnover": [(index[-5], 4.0)],
    }
    assert _call(closes, state) is None
    # The blocked rebalance must not be recorded.
    assert state["last_target"] == {"TLT": 1.0}
    assert state["turnover"] == [(index[-5], 4.0)]


def test_generic_weights_nonnegative_and_bounded():
    rng = np.random.default_rng(7)
    n = 600
    cols = {t: 100.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, n))) for t in UNIVERSE}
    closes = _frame(cols, "2024-02-14")
    weights = _call(closes, _fresh_state())
    assert weights is not None
    assert all(w >= 0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights


def test_generic_too_short_history_never_raises():
    closes = _frame({t: 100.0 * np.exp(0.001 * np.arange(10)) for t in UNIVERSE}, "2024-02-14")
    assert _call(closes, _fresh_state()) == {}


def test_generic_bot_class_metadata():
    assert QuantsmdQuarterlyTeCostGateBot.BACKTEST_PERIOD == "max"
    assert QuantsmdQuarterlyTeCostGateBot.param_grid["lookback"] == [63, 126, 189]
    assert QuantsmdQuarterlyTeCostGateBot.param_grid["te_threshold"] == [0.04, 0.06, 0.08]
