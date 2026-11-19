"""
Tests for FxMomentumLSBot (PLAN-carryblend.md S3).

Verifies:
1. Bot configuration (ALLOW_SHORT=True, SHORTABLE=FX_ETFS, CARRY_ACCRUAL=False, param_grid, tickers).
2. targetWeights sums:
   - sum of longs == -sum of shorts
   - gross sum == gross
   - net sum == 0.0 (dollar-neutral)
3. Negative weights only assigned to SHORTABLE symbols.
4. History too short returns {}:
   - fewer than window + 1 bars
   - empty dataframe
   - fewer than 2*k valid tickers
5. Ranking logic: highest momentum tickers held long, lowest held short.
6. Monthly rebalance gate:
   - rebalances on first bar of the calendar month
   - returns None mid-month when holdings are already present
   - builds book when flat live
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from tradingbot.fxmomentumlsbot import (
    FX_ETFS,
    FxMomentumLSBot,
    compute_fx_momentum_weights,
)
from tradingbot.utils.botclass import Bot

LIVE_ROWS = {"FXE": pd.Series({"timestamp": pd.Timestamp("2024-08-01"), "close": 100.0})}


@pytest.fixture
def make_fx_mom_bot(mocker):
    """Build an FxMomentumLSBot with DB initialization stubbed out."""
    mocker.patch.object(Bot, "__init__", lambda self, *args, **kwargs: None)

    def _factory(window: int = 252, k: int = 2, gross: float = 1.0):
        bot = FxMomentumLSBot(window=window, k=k, gross=gross)
        bot.bot_name = "FxMomentumLSBot"
        bot.tickers = list(FX_ETFS)
        bot.interval = "1d"
        bot.period = "max"
        bot.window = window
        bot.k = k
        bot.gross = gross
        bot.datas = {}
        return bot

    return _factory


def _make_daily_data(
    end: str,
    n_bars: int,
    tickers: tuple[str, ...] = FX_ETFS,
    growth_rates: dict[str, float] | None = None,
) -> dict[str, pd.DataFrame]:
    """Create synthetic price data for tickers ending on `end`."""
    idx = pd.bdate_range(end=end, periods=n_bars)
    rates = growth_rates or {t: 0.0001 * (i + 1) for i, t in enumerate(tickers)}
    datas: dict[str, pd.DataFrame] = {}
    for t in tickers:
        r = rates.get(t, 0.0001)
        prices = 100.0 * np.exp(np.arange(n_bars) * r)
        datas[t] = pd.DataFrame(
            {
                "open": prices,
                "high": prices,
                "low": prices,
                "close": prices,
                "volume": 1000.0,
            },
            index=idx,
        )
    return datas


# ------------------------------------------------------------------
# 1. Bot configuration
# ------------------------------------------------------------------


def test_bot_configuration(make_fx_mom_bot):
    """Verify class attributes and defaults."""
    bot = make_fx_mom_bot()
    assert bot.ALLOW_SHORT is True
    assert bot.SHORTABLE == FX_ETFS
    assert bot.CARRY_ACCRUAL is False
    assert bot.MAX_GROSS >= 1.5
    assert set(bot.tickers) == set(FX_ETFS)
    assert bot.window == 252
    assert bot.k == 2
    assert bot.gross == 1.0

    expected_grid = {
        "window": [63, 126, 189, 252, 378],
        "k": [2, 3],
        "gross": [1.0, 1.5],
    }
    assert FxMomentumLSBot.param_grid == expected_grid
    assert FxMomentumLSBot.BACKTEST_PERIOD == "max"


# ------------------------------------------------------------------
# 2. Target weights sums & dollar neutrality
# ------------------------------------------------------------------


def test_target_weights_sums_default_k2_gross1(make_fx_mom_bot):
    """Test targetWeights sums with default params (k=2, gross=1.0).

    Verifies:
    - sum of longs == -sum of shorts == gross / 2 == 0.5
    - gross sum == gross == 1.0
    - net sum == 0.0
    """
    bot = make_fx_mom_bot(window=252, k=2, gross=1.0)
    # 2024-08-01 is Thursday and starts a new calendar month (month roll)
    bot.datas = _make_daily_data(end="2024-08-01", n_bars=300)

    weights = bot.targetWeights()
    assert weights is not None
    assert len(weights) == 4  # 2 longs + 2 shorts

    longs = {k: v for k, v in weights.items() if v > 0}
    shorts = {k: v for k, v in weights.items() if v < 0}

    assert len(longs) == 2
    assert len(shorts) == 2

    sum_longs = sum(longs.values())
    sum_shorts = sum(shorts.values())
    gross_sum = sum(abs(v) for v in weights.values())
    net_sum = sum(weights.values())

    assert sum_longs == pytest.approx(0.5, abs=1e-6)
    assert sum_shorts == pytest.approx(-0.5, abs=1e-6)
    assert sum_longs == pytest.approx(-sum_shorts, abs=1e-6)
    assert gross_sum == pytest.approx(1.0, abs=1e-6)
    assert net_sum == pytest.approx(0.0, abs=1e-6)


def test_target_weights_sums_k3_gross1_5(make_fx_mom_bot):
    """Test targetWeights sums with tuned params (k=3, gross=1.5).

    Verifies:
    - sum of longs == -sum of shorts == 0.75
    - gross sum == 1.5
    - net sum == 0.0
    - each position is equal weight within side (0.25 and -0.25)
    """
    bot = make_fx_mom_bot(window=252, k=3, gross=1.5)
    bot.datas = _make_daily_data(end="2024-08-01", n_bars=300)

    weights = bot.targetWeights()
    assert weights is not None
    assert len(weights) == 6  # 3 longs + 3 shorts (full universe)

    longs = {k: v for k, v in weights.items() if v > 0}
    shorts = {k: v for k, v in weights.items() if v < 0}

    assert len(longs) == 3
    assert len(shorts) == 3

    assert sum(longs.values()) == pytest.approx(0.75, abs=1e-6)
    assert sum(shorts.values()) == pytest.approx(-0.75, abs=1e-6)
    assert sum(longs.values()) == pytest.approx(-sum(shorts.values()), abs=1e-6)
    assert sum(abs(v) for v in weights.values()) == pytest.approx(1.5, abs=1e-6)
    assert sum(weights.values()) == pytest.approx(0.0, abs=1e-6)

    for w in longs.values():
        assert w == pytest.approx(0.25, abs=1e-6)
    for w in shorts.values():
        assert w == pytest.approx(-0.25, abs=1e-6)


# ------------------------------------------------------------------
# 3. Only SHORTABLE symbols receive negative weights
# ------------------------------------------------------------------


def test_only_shortable_symbols_receive_negative_weights(make_fx_mom_bot):
    """Verify that every negative weight symbol belongs to SHORTABLE."""
    bot = make_fx_mom_bot(window=252, k=2, gross=1.0)
    bot.datas = _make_daily_data(end="2024-08-01", n_bars=300)

    weights = bot.targetWeights()
    assert weights is not None

    shorts = [sym for sym, w in weights.items() if w < 0]
    assert len(shorts) > 0
    for sym in shorts:
        assert sym in bot.SHORTABLE


# ------------------------------------------------------------------
# 4. Short history handling
# ------------------------------------------------------------------


def test_fewer_than_window_plus_one_bars_returns_empty(make_fx_mom_bot):
    """Verify that {} is returned when history has fewer than window+1 bars."""
    bot = make_fx_mom_bot(window=252, k=2)

    # Exactly 252 bars is < 252 + 1 = 253 bars
    bot.datas = _make_daily_data(end="2024-08-01", n_bars=252)
    assert bot.targetWeights() == {}

    # 10 bars
    bot.datas = _make_daily_data(end="2024-08-01", n_bars=10)
    assert bot.targetWeights() == {}

    # Empty datas
    bot.datas = {}
    assert bot.targetWeights() == {}


def test_fewer_than_2k_tickers_returns_empty(make_fx_mom_bot):
    """Verify {} is returned when fewer than 2*k tickers are available."""
    bot = make_fx_mom_bot(window=252, k=2)  # needs 2*k = 4 tickers

    # Provide only 3 tickers with sufficient bars
    three_tickers = ("FXE", "FXB", "FXA")
    bot.datas = _make_daily_data(end="2024-08-01", n_bars=300, tickers=three_tickers)
    assert bot.targetWeights() == {}


# ------------------------------------------------------------------
# 5. Ranking and momentum selection logic
# ------------------------------------------------------------------


def test_momentum_ranking_correctness(make_fx_mom_bot):
    """Verify that the top-k returning ETFs are long and the bottom-k are short."""
    bot = make_fx_mom_bot(window=252, k=2, gross=1.0)

    # Strict ordering: FXE (best) > FXB > FXA > FXC > FXF > FXY (worst)
    rates = {
        "FXE": 0.0010,  # highest return
        "FXB": 0.0008,
        "FXA": 0.0005,
        "FXC": 0.0002,
        "FXF": -0.0002,
        "FXY": -0.0005,  # lowest return
    }
    bot.datas = _make_daily_data(end="2024-08-01", n_bars=300, growth_rates=rates)

    weights = bot.targetWeights()
    assert weights is not None

    # Top 2 are FXE and FXB -> positive weights
    assert weights["FXE"] > 0
    assert weights["FXB"] > 0

    # Bottom 2 are FXF and FXY -> negative weights
    assert weights["FXF"] < 0
    assert weights["FXY"] < 0

    # Middle 2 should not be in target weights
    assert "FXA" not in weights
    assert "FXC" not in weights


# ------------------------------------------------------------------
# 6. Monthly rebalance gate
# ------------------------------------------------------------------


def test_monthly_rebalance_mid_month_with_holdings_returns_none(make_fx_mom_bot):
    """Mid-month bar with existing live holdings returns None (no rebalance)."""
    bot = make_fx_mom_bot(window=252, k=2)
    # 2024-07-15 is mid-month (Monday)
    bot.datas = _make_daily_data(end="2024-07-15", n_bars=300)

    with patch("tradingbot.utils.live_state.current_weights", return_value={"FXE": 0.25}):
        res = bot.targetWeights(LIVE_ROWS)
        assert res is None


def test_monthly_rebalance_builds_when_flat_live(make_fx_mom_bot):
    """Mid-month bar builds the book immediately when live portfolio is flat."""
    bot = make_fx_mom_bot(window=252, k=2)
    # 2024-07-15 is mid-month
    bot.datas = _make_daily_data(end="2024-07-15", n_bars=300)

    with patch("tradingbot.utils.live_state.current_weights", return_value={}):
        res = bot.targetWeights(LIVE_ROWS)
        assert res is not None
        assert len(res) == 4


# ------------------------------------------------------------------
# 7. Pure function compute_fx_momentum_weights & backtest parity
# ------------------------------------------------------------------


def test_compute_fx_momentum_weights_pure():
    """Verify pure function compute_fx_momentum_weights handles edge cases."""
    # Empty frame
    assert compute_fx_momentum_weights(pd.DataFrame(), window=252, k=2) == {}

    # Short frame
    idx = pd.bdate_range("2024-01-01", periods=100)
    df = pd.DataFrame(100.0, index=idx, columns=list(FX_ETFS))
    assert compute_fx_momentum_weights(df, window=252, k=2) == {}


def test_backtest_bot_with_fxmomentumlsbot(make_fx_mom_bot):
    """Verify FxMomentumLSBot executes cleanly inside backtest_bot with short positions."""
    from tradingbot.utils.backtest import backtest_bot

    bot = make_fx_mom_bot(window=63, k=2, gross=1.0)
    bot.benchmark_tickers = []
    bot.symbol = None
    bot.data = None
    bot.params = {}
    bot.LIQUIDATE_UNTRACKED = False

    # 300 business days of synthetic data
    rates = {
        "FXE": 0.0005,
        "FXB": 0.0003,
        "FXA": 0.0001,
        "FXC": -0.0001,
        "FXF": -0.0003,
        "FXY": -0.0005,
    }
    datas = _make_daily_data(end="2024-08-01", n_bars=300, growth_rates=rates)

    res = backtest_bot(
        bot,
        data=datas,
        initial_capital=10_000.0,
        save_to_db=False,
        save_results_to_db=False,
        record_trial=False,
    )
    assert res is not None
    assert "yearly_return" in res
    assert res["nrtrades"] > 0



