"""
Tests for live state utilities (tradingbot/utils/live_state.py).

Step S0a of PLAN-alphaideas.md:
  * Signed holdings (long, short, mixed)
  * Flat portfolio detection
  * Cash-only portfolio detection
  * Edge cases (dust cutoff, missing price, non-positive valuation)
"""

from unittest.mock import MagicMock

import pytest

from tradingbot.utils.botclass import DUST_QTY, BotSnapshot
from tradingbot.utils.live_state import current_weights


class DummyBot:
    """Minimal duck-typed bot for testing current_weights."""

    def __init__(self, portfolio: dict | None = None, pv: float = 10000.0, prices: dict | None = None):
        if portfolio is not None:
            self._db_bot = BotSnapshot(name="test_bot", portfolio=portfolio)
        else:
            self._db_bot = None
        self._pv = pv
        self._prices = prices or {}
        self.getLatestPricesBatch = MagicMock(
            side_effect=lambda syms: {s: self._prices[s] for s in syms if s in self._prices}
        )
        self.portfolio_value = MagicMock(side_effect=lambda: self._pv)

    @property
    def dbBot(self) -> BotSnapshot:
        if self._db_bot is None:
            raise AttributeError("DummyBot has no database row (dbBot)")
        return self._db_bot


def test_current_weights_flat_empty_portfolio():
    bot = DummyBot(portfolio={})
    assert current_weights(bot) == {}
    bot.getLatestPricesBatch.assert_not_called()
    bot.portfolio_value.assert_not_called()


def test_current_weights_flat_none_db_bot():
    bot = DummyBot(portfolio=None)
    assert current_weights(bot) == {}
    bot.getLatestPricesBatch.assert_not_called()
    bot.portfolio_value.assert_not_called()


def test_current_weights_flat_missing_db_bot_attribute():
    class BareBot:
        pass

    assert current_weights(BareBot()) == {}


def test_current_weights_flat_lookup_error():
    bot = MagicMock()
    type(bot).dbBot = property(lambda self: (_ for _ in ()).throw(LookupError("No row")))
    assert current_weights(bot) == {}


def test_current_weights_flat_zero_quantities():
    bot = DummyBot(portfolio={"USD": 0.0, "AAPL": 0.0, "MSFT": 0.0})
    assert current_weights(bot) == {}
    bot.getLatestPricesBatch.assert_not_called()
    bot.portfolio_value.assert_not_called()


def test_current_weights_flat_dust_quantities():
    bot = DummyBot(portfolio={"USD": 0.0, "AAPL": DUST_QTY * 0.5, "MSFT": -DUST_QTY * 0.5})
    assert current_weights(bot) == {}
    bot.getLatestPricesBatch.assert_not_called()
    bot.portfolio_value.assert_not_called()


def test_current_weights_cash_only():
    bot = DummyBot(portfolio={"USD": 10000.0})
    assert current_weights(bot) == {}
    bot.getLatestPricesBatch.assert_not_called()
    bot.portfolio_value.assert_not_called()


def test_current_weights_cash_only_with_dust_and_zeros():
    bot = DummyBot(portfolio={"USD": 10000.0, "AAPL": 0.0, "SPY": 1e-7})
    assert current_weights(bot) == {}
    bot.getLatestPricesBatch.assert_not_called()
    bot.portfolio_value.assert_not_called()


def test_current_weights_signed_holdings_long_only():
    # 10 AAPL @ $150 = $1,500; 20 MSFT @ $200 = $4,000; USD $4,500. Total = $10,000.
    portfolio = {"USD": 4500.0, "AAPL": 10.0, "MSFT": 20.0}
    prices = {"AAPL": 150.0, "MSFT": 200.0}
    bot = DummyBot(portfolio=portfolio, pv=10000.0, prices=prices)

    weights = current_weights(bot)

    assert "USD" not in weights
    assert set(weights.keys()) == {"AAPL", "MSFT"}
    assert weights["AAPL"] == pytest.approx(0.15)
    assert weights["MSFT"] == pytest.approx(0.40)

    bot.getLatestPricesBatch.assert_called_once()
    assert sorted(bot.getLatestPricesBatch.call_args[0][0]) == ["AAPL", "MSFT"]
    bot.portfolio_value.assert_called_once()


def test_current_weights_signed_holdings_short_only():
    # Short 10 TSLA @ $200 = -$2,000; USD $12,000. Total = $10,000.
    portfolio = {"USD": 12000.0, "TSLA": -10.0}
    prices = {"TSLA": 200.0}
    bot = DummyBot(portfolio=portfolio, pv=10000.0, prices=prices)

    weights = current_weights(bot)

    assert "USD" not in weights
    assert set(weights.keys()) == {"TSLA"}
    assert weights["TSLA"] == pytest.approx(-0.20)

    bot.getLatestPricesBatch.assert_called_once_with(["TSLA"])
    bot.portfolio_value.assert_called_once()


def test_current_weights_signed_holdings_mixed_long_and_short():
    # 20 SPY @ $400 = $8,000; -10 QQQ @ $300 = -$3,000; USD $5,000. Total = $10,000.
    portfolio = {"USD": 5000.0, "SPY": 20.0, "QQQ": -10.0}
    prices = {"SPY": 400.0, "QQQ": 300.0}
    bot = DummyBot(portfolio=portfolio, pv=10000.0, prices=prices)

    weights = current_weights(bot)

    assert "USD" not in weights
    assert weights["SPY"] == pytest.approx(0.80)
    assert weights["QQQ"] == pytest.approx(-0.30)


def test_current_weights_missing_price_raises_error():
    portfolio = {"USD": 5000.0, "SPY": 20.0}
    bot = DummyBot(portfolio=portfolio, pv=10000.0, prices={})

    with pytest.raises(RuntimeError, match="Missing price for symbols: \\['SPY'\\]"):
        current_weights(bot)


def test_current_weights_non_positive_portfolio_value_raises_error():
    portfolio = {"USD": 0.0, "SPY": 10.0}
    prices = {"SPY": 400.0}
    bot = DummyBot(portfolio=portfolio, pv=0.0, prices=prices)

    with pytest.raises(ValueError, match="Portfolio value is non-positive"):
        current_weights(bot)
