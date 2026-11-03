import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_intraday_gated_ema import (
    CAP,
    FAST,
    MIN_EDGE,
    SLOW,
    UNIVERSE,
    QuantsmdIntradayGatedEmaBot,
    compute_weights,
)
from tradingbot.utils.botclass import Bot


def _frame(columns: dict[str, np.ndarray], start: str = "2026-10-26 13:30") -> pd.DataFrame:
    n = len(next(iter(columns.values())))
    index = pd.date_range(start, periods=n, freq="5min")
    return pd.DataFrame(columns, index=index)


def _path(segments: list[tuple[int, float]], start: float = 100.0) -> np.ndarray:
    """Piecewise log-drift path: each (n, drift) segment adds ``drift`` per bar for n bars."""
    steps = np.concatenate([np.full(n, d) for n, d in segments])
    log_price = np.cumsum(steps) - steps[0]
    return start * np.exp(log_price)


def _same(prices: np.ndarray) -> dict[str, np.ndarray]:
    return dict.fromkeys(UNIVERSE, prices)


def test_entry_blocked_inside_first_15_minutes():
    # Bars 13:30, 13:35, 13:40: a steep rise, but every bar is inside the skip window.
    closes = _frame(_same(_path([(3, 0.01)])))
    assert compute_weights(closes) == {}
    # Same frame with the open skip removed does enter, so the gate is what blocks it.
    assert set(compute_weights(closes, skip_open_min=0)) == set(UNIVERSE)


def test_entry_blocked_when_edge_below_min_edge():
    # Steady drift 0.0001 per bar gives (fast - slow) / close of about 0.0006, below 0.0015.
    closes = _frame(_same(_path([(70, 0.0001)])))
    assert compute_weights(closes) == {}
    # Without the edge gate the same path is held, so the gate is what blocks it.
    assert set(compute_weights(closes, min_edge=0.0)) == set(UNIVERSE)


def test_entry_allowed_mid_session_on_strong_rise():
    # Flat until about 14:30, then a strong rise: entry happens mid-session and holds.
    closes = _frame(_same(_path([(12, 0.0), (48, 0.003)])))
    weights = compute_weights(closes)
    assert set(weights) == set(UNIVERSE)
    for w in weights.values():
        assert w == pytest.approx(1 / 3)
        assert w <= CAP


def test_exit_then_no_reentry_same_session():
    # Enter on the first rise, sell on the fall, then rise again strongly.
    prices = _path([(20, 0.002), (25, -0.004), (25, 0.003)])
    closes = _frame(_same(prices))
    # Sanity: the first rise alone leaves every ticker held at bar 20.
    assert set(compute_weights(closes.iloc[:20])) == set(UNIVERSE)
    # Sanity: at the end the fast average is back above the slow one, with a wide edge.
    fast = pd.Series(prices).ewm(span=FAST, adjust=False).mean().iloc[-1]
    slow = pd.Series(prices).ewm(span=SLOW, adjust=False).mean().iloc[-1]
    assert (fast - slow) / prices[-1] > MIN_EDGE
    # The ticker already traded and was sold, so it does not come back.
    assert compute_weights(closes) == {}


def test_last_bar_after_19_45_returns_empty():
    # 78 bars from 13:30 end at 19:55, past the 19:45 cut-off, so the book is flat.
    closes = _frame(_same(_path([(78, 0.002)])))
    assert closes.index[-1].strftime("%H:%M") == "19:55"
    assert compute_weights(closes) == {}


def test_last_bar_at_1940_is_still_evaluable():
    # 75 bars end at 19:40, the last bar that can still open a position.
    closes = _frame(_same(_path([(75, 0.002)])))
    assert closes.index[-1].strftime("%H:%M") == "19:40"
    assert set(compute_weights(closes)) == set(UNIVERSE)


def test_only_last_session_is_considered():
    # Day 1 rises, then goes flat, so its fast average stays above its slow one at the close.
    # Day 2 is flat. Carrying day 1's position over would hold on day 2, but day 2 has no
    # entry: its edge stays below min_edge in every entry window.
    day1 = _path([(40, 0.002), (38, 0.0)])
    day2 = np.full(40, day1[-1])
    prices = np.concatenate([day1, day2])
    closes = pd.DataFrame(
        _same(prices),
        index=pd.DatetimeIndex(
            list(pd.date_range("2026-10-26 13:30", periods=len(day1), freq="5min"))
            + list(pd.date_range("2026-10-27 13:30", periods=len(day2), freq="5min"))
        ),
    )
    # Sanity: the carried-over position would be held at the end of day 2.
    fast = pd.Series(prices).ewm(span=FAST, adjust=False).mean().iloc[-1]
    slow = pd.Series(prices).ewm(span=SLOW, adjust=False).mean().iloc[-1]
    assert fast > slow
    assert compute_weights(closes) == {}


def test_earlier_session_trade_does_not_block_today():
    # Day 1 trades and exits. Day 2 rises strongly and must enter afresh.
    day1 = _path([(20, 0.002), (20, -0.004)])
    day2 = day1[-1] * _path([(40, 0.003)]) / 100.0
    prices = np.concatenate([day1, day2])
    index = pd.DatetimeIndex(
        list(pd.date_range("2026-10-26 13:30", periods=len(day1), freq="5min"))
        + list(pd.date_range("2026-10-27 13:30", periods=len(day2), freq="5min"))
    )
    closes = pd.DataFrame(_same(prices), index=index)
    assert set(compute_weights(closes)) == set(UNIVERSE)


def test_generic_weights_nonnegative_and_bounded():
    rng = np.random.default_rng(11)
    n = 60
    cols = {}
    for t in UNIVERSE:
        steps = rng.normal(0.0, 0.002, n)
        cols[t] = 100.0 * np.exp(np.cumsum(steps))
    closes = _frame(cols)
    weights = compute_weights(closes)
    assert all(w >= 0 for w in weights.values())
    assert sum(weights.values()) <= 1.0 + 1e-9
    assert "USD" not in weights
    assert set(weights) <= set(UNIVERSE)


def test_empty_history_returns_empty():
    assert compute_weights(pd.DataFrame()) == {}


def test_bot_class_metadata_readable():
    assert QuantsmdIntradayGatedEmaBot.param_grid == {
        "min_edge": [0.001, 0.0015, 0.003],
        "fast": [9, 12],
        "slow": [21, 34],
    }
    assert QuantsmdIntradayGatedEmaBot.BACKTEST_PERIOD == "60d"


def test_bot_name_interval_period_and_universe(monkeypatch):
    captured: dict = {}

    def fake_init(self, name, **kwargs):
        captured["name"] = name
        captured.update(kwargs)

    monkeypatch.setattr(Bot, "__init__", fake_init)
    QuantsmdIntradayGatedEmaBot()
    assert captured["name"] == "QuantsmdIntradayGatedEmaBot"
    assert captured["tickers"] == list(UNIVERSE) == ["SPY", "QQQ", "IWM"]
    assert captured["interval"] == "5m"
    assert captured["period"] == "5d"
    assert captured["min_edge"] == MIN_EDGE
