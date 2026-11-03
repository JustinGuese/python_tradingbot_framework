import numpy as np
import pandas as pd
import pytest

from tradingbot import quantsmd_quarterly_te_cost_gate as base
from tradingbot.quantsmd_quarterly_te_cost_gate_gated import (
    CAP,
    LOOKBACK,
    MAX_ANNUAL_TURNOVER,
    TE_THRESHOLD,
    TE_WINDOW,
    TOP_N,
    UNIVERSE,
    QuantsmdQuarterlyTeCostGateGatedBot,
    compute_weights,
)


def _frame(columns: dict[str, np.ndarray], end: str) -> pd.DataFrame:
    n = len(next(iter(columns.values())))
    index = pd.bdate_range(end=end, periods=n)
    return pd.DataFrame(columns, index=index)


def _fresh_state() -> dict:
    return {"last_target": None, "turnover": []}


def _call(closes: pd.DataFrame, state: dict, off_scale: float = 0.0) -> dict | None:
    return compute_weights(
        closes,
        LOOKBACK,
        TOP_N,
        CAP,
        TE_THRESHOLD,
        TE_WINDOW,
        MAX_ANNUAL_TURNOVER,
        state,
        off_scale,
    )


def _universe(spy_drift: float, end: str = "2024-02-14", n: int = 300) -> pd.DataFrame:
    """Three rising names (SPY among them) and five falling names; SPY drift is set explicitly."""
    cols = {}
    for i, t in enumerate(UNIVERSE):
        drift = spy_drift if t == "SPY" else (0.003 - 0.001 * i if i < 3 else -0.002)
        cols[t] = 100.0 * np.exp(drift * np.arange(n))
    return _frame(cols, end)


def test_spy_above_sma200_book_equals_original_book():
    closes = _universe(spy_drift=0.001)
    original = base.compute_weights(
        closes,
        LOOKBACK,
        TOP_N,
        CAP,
        TE_THRESHOLD,
        TE_WINDOW,
        MAX_ANNUAL_TURNOVER,
        _fresh_state(),
    )
    gated = _call(closes, _fresh_state(), off_scale=0.0)
    assert original
    assert gated == original


def test_spy_below_sma200_goes_to_cash_with_zero_off_scale(monkeypatch):
    monkeypatch.setattr(
        base,
        "compute_weights",
        lambda *args, **kwargs: {"EFA": 0.5, "EEM": 0.5},
    )
    closes = _universe(spy_drift=-0.002)
    assert _call(closes, _fresh_state(), off_scale=0.0) == {}


def test_spy_below_sma200_halves_book_with_half_off_scale(monkeypatch):
    monkeypatch.setattr(
        base,
        "compute_weights",
        lambda *args, **kwargs: {"EFA": 0.5, "EEM": 0.5},
    )
    closes = _universe(spy_drift=-0.002)
    weights = _call(closes, _fresh_state(), off_scale=0.5)
    assert weights == {"EFA": pytest.approx(0.25), "EEM": pytest.approx(0.25)}


def test_none_from_original_passes_through(monkeypatch):
    monkeypatch.setattr(base, "compute_weights", lambda *args, **kwargs: None)
    closes = _universe(spy_drift=-0.002)
    assert _call(closes, _fresh_state(), off_scale=0.0) is None


def test_mid_quarter_no_rebalance_returns_none():
    closes = _universe(spy_drift=0.001, end="2024-02-14")
    state = {"last_target": {"SPY": 1.0}, "turnover": []}
    assert _call(closes, state, off_scale=0.0) is None


def test_short_spy_history_fails_open(monkeypatch):
    monkeypatch.setattr(
        base,
        "compute_weights",
        lambda *args, **kwargs: {"EFA": 0.5, "EEM": 0.5},
    )
    closes = _universe(spy_drift=-0.002, n=150)
    assert _call(closes, _fresh_state(), off_scale=0.0) == {"EFA": 0.5, "EEM": 0.5}


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
    assert QuantsmdQuarterlyTeCostGateGatedBot.BACKTEST_PERIOD == "max"
    assert QuantsmdQuarterlyTeCostGateGatedBot.param_grid == {"off_scale": [0.0, 0.5]}
