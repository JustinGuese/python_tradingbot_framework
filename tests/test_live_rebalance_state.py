"""
Tests for stateless live rebalance behaviour (PLAN-alphaideas.md steps A1, A2).

A1: QuantsmdQuarterlyTeCostGateBot:
  * Two fresh instances, same mid-quarter bar, same holdings, TE below threshold: both return None.
  * Flat book: builds.
  * Quarter-first bar: rebalances.
  * Tracking-error breach: rebalances early.
"""

from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from tradingbot.quantsmd_continuous_futures_trend import (
    UNIVERSE as CFT_UNIVERSE,
)
from tradingbot.quantsmd_continuous_futures_trend import (
    QuantsmdContinuousFuturesTrendBot,
)
from tradingbot.quantsmd_quarterly_te_cost_gate import (
    UNIVERSE,
    QuantsmdQuarterlyTeCostGateBot,
)
from tradingbot.quantsmd_quarterly_te_cost_gate_gated import (
    QuantsmdQuarterlyTeCostGateGatedBot,
)
from tradingbot.utils.botclass import Bot, BotSnapshot


@pytest.fixture(autouse=True)
def stub_bot_init(monkeypatch):
    """Bypass Bot.__init__ database connections and DDL in tests."""

    def _fake_init(self, name, *args, **kwargs):
        self.bot_name = name
        self.tickers = list(kwargs.get("tickers") or UNIVERSE)
        self.benchmark_tickers = ()
        self.datas = {}

    monkeypatch.setattr(Bot, "__init__", _fake_init)


def _frame(columns: dict[str, np.ndarray], end: str) -> pd.DataFrame:
    n = len(next(iter(columns.values())))
    index = pd.bdate_range(end=end, periods=n)
    return pd.DataFrame(columns, index=index)


def _three_up_five_down(end: str, n: int = 300) -> pd.DataFrame:
    cols = {}
    for i, t in enumerate(UNIVERSE):
        drift = 0.003 - 0.001 * i if i < 3 else -0.002
        cols[t] = 100.0 * np.exp(drift * np.arange(n))
    return _frame(cols, end)


def _attach_mock_state(bot, portfolio: dict, prices: dict, pv: float):
    snapshot = BotSnapshot(name=bot.bot_name, portfolio=portfolio)
    type(bot).dbBot = property(lambda self: snapshot)
    bot.getLatestPricesBatch = MagicMock(side_effect=lambda syms: {s: prices[s] for s in syms if s in prices})
    bot.portfolio_value = MagicMock(side_effect=lambda: pv)


def _setup_bot_datas(bot, df: pd.DataFrame):
    bot.datas = {t: pd.DataFrame({"close": df[t]}, index=df.index) for t in UNIVERSE}


# =========================================================================== #
# Step A1: QuantsmdQuarterlyTeCostGateBot live rebalance tests
# =========================================================================== #


def test_quarterly_te_cost_gate_live_two_fresh_instances_mid_quarter_return_none():
    """Two fresh instances, same mid-quarter bar, same holdings, TE below threshold: both return None."""
    df = _three_up_five_down("2024-02-14")
    holdings = {"USD": 0.0, "SPY": 100.0}
    prices = {"SPY": 100.0}
    pv = 10000.0

    # Instance 1: fresh CronJob run
    bot1 = QuantsmdQuarterlyTeCostGateBot()
    _setup_bot_datas(bot1, df)
    _attach_mock_state(bot1, holdings, prices, pv)

    assert bot1._state["last_target"] is None
    res1 = bot1.targetWeights({})
    assert res1 is None

    # Instance 2: another fresh CronJob run, same holdings and market state
    bot2 = QuantsmdQuarterlyTeCostGateBot()
    _setup_bot_datas(bot2, df)
    _attach_mock_state(bot2, holdings, prices, pv)

    assert bot2._state["last_target"] is None
    res2 = bot2.targetWeights({})
    assert res2 is None


def test_quarterly_te_cost_gate_live_flat_book_builds():
    """A fresh instance with a flat book builds the target book."""
    df = _three_up_five_down("2024-02-14")
    # Flat book: cash only
    holdings = {"USD": 10000.0}
    prices = {}
    pv = 10000.0

    bot = QuantsmdQuarterlyTeCostGateBot()
    _setup_bot_datas(bot, df)
    _attach_mock_state(bot, holdings, prices, pv)

    weights = bot.targetWeights({})
    assert weights is not None
    assert set(weights) == set(UNIVERSE[:3])
    for w in weights.values():
        assert w == pytest.approx(1 / 3)


def test_quarterly_te_cost_gate_live_quarter_first_bar_rebalances():
    """A fresh instance rebalances on a quarter-first bar even if tracking error is low."""
    df = _three_up_five_down("2024-04-01")
    holdings = {"USD": 0.0, "SPY": 100.0}
    prices = {"SPY": 100.0}
    pv = 10000.0

    bot = QuantsmdQuarterlyTeCostGateBot()
    _setup_bot_datas(bot, df)
    _attach_mock_state(bot, holdings, prices, pv)

    weights = bot.targetWeights({})
    assert isinstance(weights, dict)
    assert set(weights) == set(UNIVERSE[:3])


def test_quarterly_te_cost_gate_live_te_breach_rebalances_early():
    """A fresh instance rebalances early when held book's TE vs SPY exceeds threshold."""
    # Construct paths where DBC has high tracking error vs SPY over last 63 bars
    rng = np.random.default_rng(42)
    n = 300
    cols = {}
    for t in UNIVERSE:
        cols[t] = 100.0 * np.exp(0.001 * np.arange(n))
    # Add large uncorrelated noise to DBC
    cols["DBC"] = 100.0 * np.exp(0.001 * np.arange(n) + rng.normal(0, 0.08, n))
    df = _frame(cols, "2024-02-14")

    # Bot holds DBC exclusively
    holdings = {"USD": 0.0, "DBC": 100.0}
    prices = {"DBC": float(df["DBC"].iloc[-1])}
    pv = 100.0 * prices["DBC"]

    bot = QuantsmdQuarterlyTeCostGateBot()
    _setup_bot_datas(bot, df)
    _attach_mock_state(bot, holdings, prices, pv)

    weights = bot.targetWeights({})
    assert isinstance(weights, dict)
    assert len(weights) > 0


def test_quarterly_te_cost_gate_gated_live_stateless():
    """QuantsmdQuarterlyTeCostGateGatedBot also respects stateless live rebalancing."""
    df = _three_up_five_down("2024-02-14")

    # Instance with matching holdings mid-quarter -> None
    bot_held = QuantsmdQuarterlyTeCostGateGatedBot()
    _setup_bot_datas(bot_held, df)
    _attach_mock_state(bot_held, {"USD": 0.0, "SPY": 100.0}, {"SPY": 100.0}, 10000.0)
    assert bot_held.targetWeights({}) is None

    # Instance with flat book -> builds
    bot_flat = QuantsmdQuarterlyTeCostGateGatedBot()
    _setup_bot_datas(bot_flat, df)
    _attach_mock_state(bot_flat, {"USD": 10000.0}, {}, 10000.0)
    weights = bot_flat.targetWeights({})
    assert weights is not None
    assert len(weights) > 0


# =========================================================================== #
# Step A2: QuantsmdContinuousFuturesTrendBot live rebalance tests
# =========================================================================== #


def _cft_frame(drifts: dict[str, float], end: str = "2024-02-15", periods: int = 600) -> pd.DataFrame:
    index = pd.bdate_range(end=end, periods=periods)
    rng = np.random.default_rng(7)
    data = {}
    for ticker, drift in drifts.items():
        noise = rng.normal(0.0, 0.004, size=periods)
        data[ticker] = 100.0 * np.exp(np.cumsum(drift + noise))
    return pd.DataFrame(data, index=index)


def test_continuous_futures_trend_live_flat_book_mid_month_with_uptrends_builds():
    """Flat book on a mid-month bar with up-trends returns non-empty weights."""
    drifts = dict.fromkeys(CFT_UNIVERSE, 0.003)
    df = _cft_frame(drifts, end="2024-02-15")  # mid-month

    bot = QuantsmdContinuousFuturesTrendBot()
    bot.datas = {t: pd.DataFrame({"close": df[t]}, index=df.index) for t in CFT_UNIVERSE}
    _attach_mock_state(bot, portfolio={"USD": 10000.0}, prices={}, pv=10000.0)

    weights = bot.targetWeights({})
    assert weights is not None
    assert len(weights) > 0
    assert set(weights) == set(CFT_UNIVERSE)
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_continuous_futures_trend_live_held_book_mid_month_returns_none():
    """Holding present on a mid-month bar returns None."""
    drifts = dict.fromkeys(CFT_UNIVERSE, 0.003)
    df = _cft_frame(drifts, end="2024-02-15")  # mid-month

    bot = QuantsmdContinuousFuturesTrendBot()
    bot.datas = {t: pd.DataFrame({"close": df[t]}, index=df.index) for t in CFT_UNIVERSE}
    # Persisted holding present: 10 shares of SPY
    holdings = {"USD": 5000.0, "SPY": 10.0}
    prices = {"SPY": float(df["SPY"].iloc[-1])}
    pv = 5000.0 + 10.0 * prices["SPY"]
    _attach_mock_state(bot, portfolio=holdings, prices=prices, pv=pv)

    res = bot.targetWeights({})
    assert res is None


def test_continuous_futures_trend_live_flat_book_mid_month_no_uptrends_returns_none():
    """Flat book on mid-month bar with all downtrends returns None."""
    drifts = dict.fromkeys(CFT_UNIVERSE, -0.003)
    df = _cft_frame(drifts, end="2024-02-15")  # mid-month

    bot = QuantsmdContinuousFuturesTrendBot()
    bot.datas = {t: pd.DataFrame({"close": df[t]}, index=df.index) for t in CFT_UNIVERSE}
    _attach_mock_state(bot, portfolio={"USD": 10000.0}, prices={}, pv=10000.0)

    res = bot.targetWeights({})
    assert res is None
