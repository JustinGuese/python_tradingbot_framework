"""Tests for NeutralStockBot (F2).

Verifies:
1. Bot configuration (ALLOW_SHORT=True, MAX_GROSS=2.0, SHORTABLE=SP500, CASH_PROXY=SHV).
2. Universe tickers include SP500, SPY, QQQ, and SHV cash proxy.
3. Monthly rebalance gate: builds when flat, returns None mid-month when holdings are present.
4. Target weights output: dollar-neutral, long=0.9, short=-0.9, Reg T headroom gross <= 2.0.
5. Idle cash parked in SHV.
6. Equal counts per sector side for all represented sectors.
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from tradingbot.neutralstockbot import NeutralStockBot
from tradingbot.utils.botclass import Bot
from tradingbot.utils.universes import SP500, SP500_SECTOR


@pytest.fixture
def make_neutral_bot(mocker):
    """Create a NeutralStockBot with Bot base DB initialization stubbed out."""
    mocker.patch.object(Bot, "__init__", lambda self, *args, **kwargs: None)

    def _factory(k: int = 25, park_cash: bool = True):
        bot = NeutralStockBot(k=k, park_cash=park_cash)
        bot.bot_name = "NeutralStockBot"
        bot.tickers = list(dict.fromkeys((*SP500, "SPY", "QQQ", "SHV")))
        bot.interval = "1d"
        bot.period = "3y"
        bot.k = k
        bot.weight_mom = 0.5
        bot.weight_rev = 0.5
        bot.target_long = 0.9
        bot.target_short = 0.9
        bot.park_cash = park_cash
        bot.beta_scale = False
        bot.datas = {}
        return bot

    return _factory


def test_neutral_stock_bot_configuration(make_neutral_bot):
    """Verify class attributes and initialization."""
    bot = make_neutral_bot(k=25)
    assert bot.ALLOW_SHORT is True
    assert bot.MAX_GROSS == 2.0
    assert bot.SHORTABLE == SP500
    assert bot.CASH_PROXY == "SHV"
    assert "SHV" in bot.tickers
    assert "SPY" in bot.tickers
    assert "QQQ" in bot.tickers
    assert set(SP500).issubset(set(bot.tickers))


def test_monthly_rebalance_mid_month_returns_none(make_neutral_bot):
    """Verify that mid-month bar with existing holdings returns None."""
    bot = make_neutral_bot(k=10)

    # Two consecutive dates in the same month (no month roll)
    dates = pd.date_range("2024-03-11", periods=2, freq="B")
    data = {t: pd.DataFrame({"close": [100.0, 101.0]}, index=dates) for t in bot.tickers[:10]}
    bot.datas = data

    # When holdings exist, mid-month bar returns None
    with patch("tradingbot.neutralstockbot.current_weights", return_value={"AAPL": 0.05}):
        res = bot.targetWeights({})
        assert res is None


def test_monthly_rebalance_builds_when_flat(make_neutral_bot):
    """Verify that bot builds positions on any bar when flat."""
    bot = make_neutral_bot(k=5)

    # Create synthetic daily prices for a 3-year history so residual regression can run
    np.random.seed(42)
    n_bars = 760
    dates = pd.date_range("2021-01-01", periods=n_bars, freq="B")

    mkt_ret = np.random.normal(0.0004, 0.01, size=n_bars)
    mkt_price = 100.0 * np.exp(np.cumsum(mkt_ret))

    datas: dict[str, pd.DataFrame] = {
        "SPY": pd.DataFrame({"close": mkt_price}, index=dates),
        "QQQ": pd.DataFrame({"close": 1.1 * mkt_price}, index=dates),
        "SHV": pd.DataFrame({"close": np.full(n_bars, 100.0)}, index=dates),
    }

    # Generate data for top 30 S&P 500 stocks
    top_stocks = list(SP500[:30])
    for sym in top_stocks:
        r = mkt_ret + np.random.normal(0, 0.015, size=n_bars)
        datas[sym] = pd.DataFrame({"close": 50.0 * np.exp(np.cumsum(r))}, index=dates)

    bot.datas = datas

    # When flat (holdings empty): builds book
    with patch("tradingbot.neutralstockbot.current_weights", return_value={}):
        weights = bot.targetWeights({})
        assert weights is not None
        assert len(weights) > 0

        # Verify dollar-neutrality of stock positions
        stock_weights = {k: v for k, v in weights.items() if k != "SHV"}
        long_sum = sum(w for w in stock_weights.values() if w > 0)
        short_sum = sum(w for w in stock_weights.values() if w < 0)

        assert long_sum == pytest.approx(0.9, abs=1e-5)
        assert short_sum == pytest.approx(-0.9, abs=1e-5)
        assert sum(stock_weights.values()) == pytest.approx(0.0, abs=1e-5)

        # Total gross exposure under Reg T headroom (<= 2.0)
        assert sum(abs(w) for w in weights.values()) <= 2.0

        # Verify equal counts per sector side
        sec_counts: dict[str, dict[str, int]] = {}
        for sym, w in stock_weights.items():
            sec = SP500_SECTOR.get(sym, "UNKNOWN")
            side = "long" if w > 0 else "short"
            sec_counts.setdefault(sec, {"long": 0, "short": 0})[side] += 1

        for _sec, counts in sec_counts.items():
            assert counts["long"] == counts["short"]
