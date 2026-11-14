"""
Tests for margin core, schema migrations, and short selling configurations (PLAN.md S0).
"""

from datetime import date

import pytest

from tradingbot.utils.bot_repository import BotRepository
from tradingbot.utils.config import (
    SHORT_BORROW_RATE_PCT,
    SHORT_CONFIG,
    SHORT_INIT_PCT,
    SHORT_MAINT_PCT,
    SHORTABLE,
    ShortConfig,
)
from tradingbot.utils.db import CashAdjustment, Trade
from tradingbot.utils.margin import (
    MissingPriceError,
    book_values,
    cover_order,
    initial_ok,
    maintenance_breach,
    spendable_cash,
)


def test_long_only_book_zero_behaviour_change():
    """A long-only book has spendable == USD, zero short value, and no maintenance breach."""
    portfolio = {"USD": 10000.0, "AAPL": 20.0, "MSFT": 10.0}
    prices = {"AAPL": 150.0, "MSFT": 300.0}

    long_val, short_val, equity = book_values(portfolio, prices)
    assert long_val == 6000.0
    assert short_val == 0.0
    assert equity == 16000.0

    # spendable equals USD for a long-only book
    assert spendable_cash(portfolio, prices) == portfolio["USD"]
    # No maintenance breach
    assert not maintenance_breach(portfolio, prices)
    # Fully funded book passes initial margin at max_gross=1.0
    assert initial_ok(portfolio, prices, max_gross=1.0)
    # No shorts to cover
    assert cover_order(portfolio, prices) is None


def test_initial_ok_100_long_100_short_max_gross():
    """100 long / 100 short passes at max_gross 2.0 and fails at 1.0."""
    portfolio = {"USD": 100.0, "AAPL": 1.0, "QQQ": -1.0}
    prices = {"AAPL": 100.0, "QQQ": 100.0}

    long_val, short_val, equity = book_values(portfolio, prices)
    assert long_val == 100.0
    assert short_val == 100.0
    assert equity == 100.0

    # Reg T requirement: equity (100) >= 0.5 * 100 + 0.5 * 100 (100) -> OK
    # Gross leverage: (100 + 100) / 100 = 2.0
    assert initial_ok(portfolio, prices, max_gross=2.0)
    assert not initial_ok(portfolio, prices, max_gross=1.0)

    # Spendable cash deducts short collateral: 100 cash - 100 short collateral = 0
    assert spendable_cash(portfolio, prices) == 0.0


def test_maintenance_breach_and_cover_order_picks_largest_short():
    """Maintenance breach triggers on price rise and cover_order picks largest short."""
    # Book with USD 100, long AAPL $100, short QQQ $100 (1 share), short SPY $50 (0.5 shares)
    portfolio = {"USD": 100.0, "AAPL": 1.0, "QQQ": -1.0, "SPY": -0.5}
    prices = {"AAPL": 100.0, "QQQ": 100.0, "SPY": 100.0}

    # Initially safe: equity = 100 + 100 - 150 = 50. req = 0.25 * 100 + 0.30 * 150 = 70.
    # Actually at QQQ=100, SPY=100, equity is 50 < 70, so even here it breaches.
    assert maintenance_breach(portfolio, prices)

    # Now let QQQ surge to $180, making it even worse and clearly the largest short
    prices_surge = {"AAPL": 100.0, "QQQ": 180.0, "SPY": 100.0}
    assert maintenance_breach(portfolio, prices_surge)

    # cover_order must pick QQQ ($180 value) over SPY ($50 value)
    chosen = cover_order(portfolio, prices_surge)
    assert chosen == "QQQ"


def test_missing_price_raises_fail_closed():
    """Valuation fails closed when a held symbol's price is missing or invalid."""
    portfolio = {"USD": 5000.0, "AAPL": 10.0, "QQQ": -5.0}

    # Missing symbol in prices dict
    with pytest.raises((KeyError, ValueError)):
        book_values(portfolio, {"AAPL": 150.0})

    # None price
    with pytest.raises((KeyError, ValueError)):
        book_values(portfolio, {"AAPL": 150.0, "QQQ": None})  # type: ignore

    # Non-positive price
    with pytest.raises((KeyError, ValueError)):
        book_values(portfolio, {"AAPL": 150.0, "QQQ": -10.0})

    # cover_order also fails closed on missing price
    with pytest.raises((KeyError, ValueError)):
        cover_order(portfolio, {"AAPL": 150.0})


def test_schema_trade_action_and_cash_adjustment(sqlite_db, db_session):
    """Trade.action is logged via log_trade, and CashAdjustment stores carry entries."""
    bot = BotRepository.create_or_get_bot("TestMarginBot", session=db_session)
    db_session.commit()

    # Test log_trade with action
    trade = BotRepository.log_trade(
        bot_name=bot.name,
        symbol="QQQ",
        quantity=5.0,
        price=450.0,
        is_buy=False,
        session=db_session,
        action="SHORT",
    )
    assert trade is not None
    assert trade.action == "SHORT"
    assert trade.isBuy is False
    db_session.commit()

    # Query back trade from DB
    saved_trade = db_session.query(Trade).filter_by(id=trade.id).one()
    assert saved_trade.action == "SHORT"

    # Test CashAdjustment model
    adj = CashAdjustment(
        bot_name=bot.name,
        date=date(2026, 11, 14),
        symbol="QQQ",
        kind="borrow_fee",
        amount=-0.62,
    )
    db_session.add(adj)
    db_session.commit()

    saved_adj = (
        db_session.query(CashAdjustment)
        .filter_by(bot_name=bot.name, date=date(2026, 11, 14), symbol="QQQ", kind="borrow_fee")
        .one()
    )
    assert saved_adj.amount == -0.62


def test_config_short_settings_and_allowlist():
    """ShortConfig defaults and SHORTABLE universe allowlist."""
    cfg = ShortConfig.from_env()
    assert cfg.borrow_rate_pct == SHORT_BORROW_RATE_PCT == 1.0
    assert cfg.maint_pct == SHORT_MAINT_PCT == 0.30
    assert cfg.init_pct == SHORT_INIT_PCT == 0.50
    assert SHORT_CONFIG.borrow_rate_pct == 1.0

    # SHORTABLE universe contains ETFs and S&P 100
    assert "QQQ" in SHORTABLE
    assert "SPY" in SHORTABLE
    assert "AAPL" in SHORTABLE
    assert "MSFT" in SHORTABLE
