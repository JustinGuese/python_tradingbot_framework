from unittest.mock import MagicMock

from tradingbot.utils.bot_repository import BotRepository
from tradingbot.utils.config import EXECUTION_CONFIG
from tradingbot.utils.portfolio_manager import PortfolioManager


def test_portfolio_manager_buy(db_session, test_bot):
    # Mock data service
    mock_ds = MagicMock()
    mock_ds.get_latest_price.return_value = 100.0

    # Initial state
    assert test_bot.portfolio["USD"] == 10000.0

    pm = PortfolioManager(test_bot, test_bot.name, mock_ds, BotRepository)

    # Execute buy in a session to test transactional behavior
    pm.buy("AAPL", quantity_usd=1000.0, session=db_session)

    # Check results
    updated_bot = db_session.query(type(test_bot)).filter_by(name=test_bot.name).one()
    # Cash is debited by the full gross budget regardless of costs.
    assert updated_bot.portfolio["USD"] == 9000.0
    # Shares bought are reduced by slippage: 1000 / (100 * 1.0005).
    assert updated_bot.portfolio["AAPL"] == 1000.0 / EXECUTION_CONFIG.buy_execution_price(100.0)


def test_portfolio_manager_sell(db_session, test_bot):
    # Setup initial holdings
    test_bot.portfolio = {"USD": 5000.0, "AAPL": 10.0}
    BotRepository.update_bot(test_bot, session=db_session)

    # Mock data service
    mock_ds = MagicMock()
    mock_ds.get_latest_price.return_value = 150.0

    pm = PortfolioManager(test_bot, test_bot.name, mock_ds, BotRepository)

    # Execute sell
    pm.sell("AAPL", quantity_usd=750.0, session=db_session)

    # Check results
    updated_bot = db_session.query(type(test_bot)).filter_by(name=test_bot.name).one()
    # quantity_usd is position notional at the REFERENCE price, so the share
    # count is exactly 750/150 = 5 and slippage does not move it.
    assert updated_bot.portfolio["AAPL"] == 5.0
    # Cash received is net of slippage: 5 * (150 * 0.9995).
    assert updated_bot.portfolio["USD"] == 5000.0 + 5.0 * EXECUTION_CONFIG.sell_execution_price(150.0)


def test_rebalance_portfolio(sqlite_db, db_session, test_bot):
    # rebalance_portfolio opens its own transaction instead of taking one, so
    # without the sqlite_db fixture this test connects to the real POSTGRES_URI.
    # Mock data service
    mock_ds = MagicMock()
    mock_ds.get_latest_prices_batch.return_value = {"AAPL": 100.0, "GOOG": 200.0}
    mock_ds.get_latest_price.side_effect = lambda sym, cached: {"AAPL": 100.0, "GOOG": 200.0}[sym]

    pm = PortfolioManager(test_bot, test_bot.name, mock_ds, BotRepository)

    # Target: 50% AAPL, 50% GOOG (Total worth $10,000)
    target = {"AAPL": 0.5, "GOOG": 0.5}

    pm.rebalance_portfolio(target)

    # rebalance_portfolio committed in its own session; without expiring, this
    # session hands back its cached pre-rebalance copy from the identity map.
    db_session.expire_all()

    # Check results
    updated_bot = db_session.query(type(test_bot)).filter_by(name=test_bot.name).one()
    assert updated_bot.portfolio["AAPL"] == 5000.0 / EXECUTION_CONFIG.buy_execution_price(100.0)
    assert updated_bot.portfolio["GOOG"] == 5000.0 / EXECUTION_CONFIG.buy_execution_price(200.0)
    assert updated_bot.portfolio.get("USD", 0) < 1.0  # Should be ~0
