from unittest.mock import MagicMock

import pytest

from tradingbot.utils.bot_repository import BotRepository
from tradingbot.utils.config import ExecutionConfig
from tradingbot.utils.db import Trade
from tradingbot.utils.margin import MissingPriceError
from tradingbot.utils.portfolio_manager import PortfolioManager


def test_sell_never_goes_negative(sqlite_db, db_session, test_bot):
    # Setup initial holdings
    test_bot.portfolio = {"USD": 5000.0, "AAPL": 10.0}
    BotRepository.update_bot(test_bot, session=db_session)

    mock_ds = MagicMock()
    mock_ds.get_latest_price.return_value = 100.0

    pm = PortfolioManager(test_bot.name, mock_ds, BotRepository)

    # Attempt to sell more than held (2000 USD at $100 = 20 shares, have only 10)
    pm.sell("AAPL", quantity_usd=2000.0, session=db_session)

    db_session.expire_all()
    updated_bot = db_session.query(type(test_bot)).filter_by(name=test_bot.name).one()
    # Position was completely sold, never goes negative
    assert "AAPL" not in updated_bot.portfolio or updated_bot.portfolio["AAPL"] == 0.0

    # Sell again when holding is 0
    pm.sell("AAPL", quantity_usd=500.0, session=db_session)
    db_session.expire_all()
    updated_bot = db_session.query(type(test_bot)).filter_by(name=test_bot.name).one()
    assert "AAPL" not in updated_bot.portfolio or updated_bot.portfolio["AAPL"] == 0.0

    # Verify action logged
    trade = db_session.query(Trade).filter_by(bot_name=test_bot.name, symbol="AAPL").first()
    assert trade is not None
    assert trade.action == "SELL"
    assert not trade.isBuy


def test_short_then_cover_roundtrips_cash(sqlite_db, db_session, test_bot):
    # Zero slippage and commission to verify exact round-trip cash conservation
    zero_cost_cfg = ExecutionConfig(commission_pct=0.0, slippage_pct=0.0)

    assert test_bot.portfolio["USD"] == 10000.0

    mock_ds = MagicMock()
    mock_ds.get_latest_price.return_value = 100.0
    mock_ds.get_latest_prices_batch.return_value = {"QQQ": 100.0}

    pm = PortfolioManager(test_bot.name, mock_ds, BotRepository, execution_config=zero_cost_cfg)

    # Short 2000 USD of QQQ at $100/sh -> 20 shares shorted
    proceeds = pm.short("QQQ", quantity_usd=2000.0, session=db_session)
    assert proceeds == 2000.0

    db_session.expire_all()
    updated_bot = db_session.query(type(test_bot)).filter_by(name=test_bot.name).one()
    assert updated_bot.portfolio["QQQ"] == -20.0
    assert updated_bot.portfolio["USD"] == 12000.0

    # Cover entire short position
    cost = pm.cover("QQQ", quantity_usd=-1, session=db_session)
    assert cost == -2000.0

    db_session.expire_all()
    updated_bot = db_session.query(type(test_bot)).filter_by(name=test_bot.name).one()
    assert "QQQ" not in updated_bot.portfolio
    # Cash returns exactly to initial state
    assert updated_bot.portfolio["USD"] == 10000.0

    # Verify trade logging
    trades = db_session.query(Trade).filter_by(bot_name=test_bot.name, symbol="QQQ").order_by(Trade.id.asc()).all()
    assert len(trades) == 2
    assert trades[0].action == "SHORT"
    assert not trades[0].isBuy
    assert trades[0].quantity == 20.0
    assert trades[1].action == "COVER"
    assert trades[1].isBuy
    assert trades[1].quantity == 20.0


def test_rebalance_max_gross_check(sqlite_db, db_session, test_bot):
    zero_cost_cfg = ExecutionConfig(commission_pct=0.0, slippage_pct=0.0)

    mock_ds = MagicMock()
    mock_ds.get_latest_prices_batch.return_value = {"AAPL": 100.0, "QQQ": 100.0}
    mock_ds.get_latest_price.side_effect = lambda sym, cached=None: {"AAPL": 100.0, "QQQ": 100.0}[sym]

    pm = PortfolioManager(test_bot.name, mock_ds, BotRepository, execution_config=zero_cost_cfg)

    # Target: 100% Long AAPL, -100% Short QQQ, 100% USD cash (gross = 2.0, net = 1.0)
    target = {"AAPL": 1.0, "QQQ": -1.0, "USD": 1.0}

    # At max_gross=1.0, gross leverage 2.0 exceeds limit and is refused whole
    with pytest.raises(ValueError, match="margin"):
        pm.rebalance_portfolio(target, allow_short=True, max_gross=1.0)

    db_session.expire_all()
    bot = db_session.query(type(test_bot)).filter_by(name=test_bot.name).one()
    assert bot.portfolio == {"USD": 10000.0}
    assert db_session.query(Trade).filter_by(bot_name=test_bot.name).count() == 0

    # At max_gross=2.0, gross leverage 2.0 is allowed and passes
    pm.rebalance_portfolio(target, allow_short=True, max_gross=2.0)

    db_session.expire_all()
    bot = db_session.query(type(test_bot)).filter_by(name=test_bot.name).one()
    assert bot.portfolio["AAPL"] == 100.0
    assert bot.portfolio["QQQ"] == -100.0
    assert bot.portfolio["USD"] == 10000.0

    trades = {t.symbol: t for t in db_session.query(Trade).filter_by(bot_name=test_bot.name).all()}
    assert trades["AAPL"].action == "BUY"
    assert trades["AAPL"].isBuy
    assert trades["QQQ"].action == "SHORT"
    assert not trades["QQQ"].isBuy


def test_non_shortable_symbol_refused(sqlite_db, db_session, test_bot):
    mock_ds = MagicMock()
    mock_ds.get_latest_price.return_value = 10.0
    mock_ds.get_latest_prices_batch.return_value = {"UNLISTED_PENNY": 10.0}

    pm = PortfolioManager(test_bot.name, mock_ds, BotRepository)

    # short() refuses symbol outside shortable
    with pytest.raises(ValueError, match="not shortable"):
        pm.short("UNLISTED_PENNY", quantity_usd=1000.0, session=db_session)

    # rebalance_portfolio refuses symbol outside shortable
    with pytest.raises(ValueError, match="not shortable"):
        pm.rebalance_portfolio({"UNLISTED_PENNY": -0.5, "USD": 1.5}, allow_short=True)


def test_naked_option_underlying_uses_options_margin(sqlite_db, db_session, test_bot):
    mock_ds = MagicMock()
    mock_ds.get_latest_price.return_value = 150.0

    pm = PortfolioManager(test_bot.name, mock_ds, BotRepository)

    # Stock leg alone in trade_option_legs must be beside options
    with pytest.raises(ValueError, match="beside options"):
        pm.trade_option_legs([("AAPL", -10)], session=db_session)

    # Naked short call in trade_legs requires option margin (which is infinite, refusing the trade)
    call_symbol = "AAPL261218C00200000"
    with pytest.raises(ValueError, match="margin"):
        pm.trade_legs([(call_symbol, -100)], session=db_session)


def test_missing_price_on_short_raises(sqlite_db, db_session, test_bot):
    test_bot.portfolio = {"USD": 10000.0, "QQQ": -10.0}
    BotRepository.update_bot(test_bot, session=db_session)

    mock_ds = MagicMock()
    mock_ds.get_latest_prices_batch.return_value = {"QQQ": None}
    mock_ds.get_latest_price.return_value = None

    pm = PortfolioManager(test_bot.name, mock_ds, BotRepository)

    # Missing price on a short holding raises MissingPriceError
    with pytest.raises(MissingPriceError):
        pm.rebalance_portfolio({"USD": 1.0}, allow_short=True)

    with pytest.raises(MissingPriceError):
        pm.total_value()

    with pytest.raises(MissingPriceError):
        pm.cover("QQQ", session=db_session)


def test_fractional_short_shares_refused(sqlite_db, db_session, test_bot):
    mock_ds = MagicMock()
    mock_ds.get_latest_price.return_value = 100.0
    mock_ds.get_latest_prices_batch.return_value = {"QQQ": 100.0}

    pm = PortfolioManager(test_bot.name, mock_ds, BotRepository)

    # Short position with fractional shares must be rejected
    with pytest.raises(ValueError, match="Whole shares"):
        pm.trade_legs([("QQQ", -10.5)], session=db_session)
