import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_futures_donchian_contracts import (
    SPECS,
    UNIVERSE,
    QuantsmdFuturesDonchianContractsBot,
    compute_weights,
)


def _frame(closes: np.ndarray, half_range: float = 0.5) -> pd.DataFrame:
    index = pd.bdate_range("2020-01-01", periods=len(closes))
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes + half_range,
            "low": closes - half_range,
            "close": closes,
        },
        index=index,
    )


def _breakout_closes(drift: np.ndarray | None = None) -> np.ndarray:
    # 60 flat bars at 100, a breakout to 110, then a tail that stays above the channel low.
    tail = np.full(20, 105.0) if drift is None else drift
    return np.concatenate([np.full(60, 100.0), [110.0], tail])


def test_too_few_rows_returns_empty():
    ohlc = {"GC=F": _frame(np.full(55, 100.0))}
    assert compute_weights(ohlc) == {}


def test_breakout_then_stays_inside_band_is_long():
    drift = np.linspace(110.0, 104.0, 20)
    ohlc = {"GC=F": _frame(_breakout_closes(drift))}
    weights = compute_weights(ohlc)
    assert set(weights) == {"GC=F"}
    assert 0.0 < weights["GC=F"] <= 0.25


def test_breakout_then_held_at_last_bar_gives_exact_margin_cap():
    # GC=F: 0.15 * 105 * 100 / 9000 = 0.175 is below the 0.005 * close / ATR cap
    # and the 0.25 cap, so the margin cap binds.
    ohlc = {"GC=F": _frame(_breakout_closes())}
    weights = compute_weights(ohlc)
    assert weights == pytest.approx({"GC=F": 0.175})


def test_breakout_then_break_below_channel_low_goes_flat():
    closes = np.concatenate([_breakout_closes(), [90.0]])
    ohlc = {"GC=F": _frame(closes)}
    assert compute_weights(ohlc) == {}


def test_flat_market_without_breakout_returns_empty():
    ohlc = {t: _frame(np.full(120, 100.0)) for t in UNIVERSE}
    assert compute_weights(ohlc) == {}


def test_cap_binds_on_low_margin_ratio_contract():
    # CL=F: ATR of 1 gives 0.005 * 105 / 1 = 0.525, the margin cap is far above, so 0.25 binds.
    ohlc = {"CL=F": _frame(_breakout_closes())}
    weights = compute_weights(ohlc)
    assert weights == pytest.approx({"CL=F": 0.25})


def test_generic_weights_nonnegative_and_bounded():
    rng = np.random.default_rng(11)
    ohlc = {}
    for t in UNIVERSE:
        steps = rng.normal(0.0005, 0.01, 400)
        closes = 100.0 * np.exp(np.cumsum(steps))
        ohlc[t] = _frame(closes, half_range=0.4)
    weights = compute_weights(ohlc)
    assert all(w >= 0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights
    assert set(weights) <= set(UNIVERSE)


def test_generic_too_short_history_never_raises():
    ohlc = {t: _frame(np.full(10, 100.0)) for t in UNIVERSE}
    assert compute_weights(ohlc) == {}


def test_generic_bot_class_metadata_readable():
    assert QuantsmdFuturesDonchianContractsBot.param_grid == {"window": [20, 55, 100]}
    assert hasattr(QuantsmdFuturesDonchianContractsBot, "BACKTEST_PERIOD")
    assert UNIVERSE == ["MES=F", "MNQ=F", "CL=F", "GC=F"]
    assert set(SPECS) == set(UNIVERSE)
