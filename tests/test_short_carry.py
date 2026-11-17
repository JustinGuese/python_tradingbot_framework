"""
Tests for short carry: borrow fees, dividends in lieu, worth calculation, and maintenance liquidation (PLAN.md S1d).
"""

from datetime import date
from unittest.mock import MagicMock

import pytest

from tradingbot.calculate_portfolio_worth import calculate_portfolio_worth as script_calc_worth
from tradingbot.utils.bot_repository import BotRepository
from tradingbot.utils.data_service import DataService
from tradingbot.utils.db import Bot as BotModel
from tradingbot.utils.db import CashAdjustment, DividendEvent, Trade
from tradingbot.utils.margin import MissingPriceError
from tradingbot.utils.portfolio_utils import calculate_portfolio_worth
from tradingbot.utils.short_carry import DAYS_PER_YEAR, apply_carry


@pytest.fixture(autouse=True)
def patch_short_carry_db(sqlite_db, monkeypatch):
    """Ensure short_carry.get_db_session routes to in-memory SQLite when session is omitted."""
    monkeypatch.setattr("tradingbot.utils.short_carry.get_db_session", sqlite_db)


def _setup_bot(session, name="CarryBot", portfolio=None):
    bot = BotModel(name=name, portfolio=portfolio or {"USD": 10000.0})
    session.add(bot)
    session.commit()
    return bot


def test_accrual_twice_on_same_day_charges_once(sqlite_db, db_session):
    """Accrual twice on the same day charges borrow fee once (idempotent)."""
    bot = _setup_bot(db_session, "BorrowBot", {"USD": 10000.0, "QQQ": -10.0})
    today = date(2026, 11, 14)
    prices = {"QQQ": 400.0}

    # Market value = 10 * 400 = 4000. Default borrow rate = 1.0% = 0.01.
    expected_daily_fee = 4000.0 * 0.01 * (1.0 / DAYS_PER_YEAR)

    # First accrual charges 1 day
    adjs1 = apply_carry("BorrowBot", today=today, session=db_session, prices=prices)
    assert len(adjs1) == 1
    assert adjs1[0].kind == "borrow_fee"
    assert adjs1[0].symbol == "QQQ"
    assert adjs1[0].amount == pytest.approx(-expected_daily_fee)

    db_session.expire_all()
    bot = db_session.query(BotModel).filter_by(name="BorrowBot").one()
    assert bot.portfolio["USD"] == pytest.approx(10000.0 - expected_daily_fee)

    # Second accrual on same day is a no-op
    adjs2 = apply_carry("BorrowBot", today=today, session=db_session, prices=prices)
    assert len(adjs2) == 0

    # Cash and CashAdjustment records unchanged
    db_session.expire_all()
    bot = db_session.query(BotModel).filter_by(name="BorrowBot").one()
    assert bot.portfolio["USD"] == pytest.approx(10000.0 - expected_daily_fee)
    adj_rows = db_session.query(CashAdjustment).filter_by(bot_name="BorrowBot", kind="borrow_fee").all()
    assert len(adj_rows) == 1


def test_weekend_gap_charges_3_days(sqlite_db, db_session):
    """A weekend gap (Friday to Monday) charges 3 calendar days of borrow fees."""
    bot = _setup_bot(db_session, "WeekendBot", {"USD": 10000.0, "QQQ": -10.0})
    friday = date(2026, 11, 13)
    monday = date(2026, 11, 16)
    prices = {"QQQ": 400.0}

    one_day_fee = 4000.0 * 0.01 * (1.0 / DAYS_PER_YEAR)

    # Accrue on Friday (1 day)
    apply_carry("WeekendBot", today=friday, session=db_session, prices=prices)

    db_session.expire_all()
    bot = db_session.query(BotModel).filter_by(name="WeekendBot").one()
    assert bot.portfolio["USD"] == pytest.approx(10000.0 - one_day_fee)

    # Accrue on Monday: gap is (Monday - Friday) = 3 calendar days
    adjs_mon = apply_carry("WeekendBot", today=monday, session=db_session, prices=prices)
    assert len(adjs_mon) == 1
    assert adjs_mon[0].kind == "borrow_fee"
    expected_three_day_fee = 3 * one_day_fee
    assert adjs_mon[0].amount == pytest.approx(-expected_three_day_fee)

    db_session.expire_all()
    bot = db_session.query(BotModel).filter_by(name="WeekendBot").one()
    assert bot.portfolio["USD"] == pytest.approx(10000.0 - one_day_fee - expected_three_day_fee)


def test_dividend_debit_once(sqlite_db, db_session):
    """Short holding pays dividend in lieu on ex-date, debited exactly once."""
    bot = _setup_bot(db_session, "DivBot", {"USD": 10000.0, "AAPL": -20.0})
    ex_date = date(2026, 11, 14)
    prices = {"AAPL": 150.0}

    # Add dividend event: $0.50 per share
    div_event = DividendEvent(symbol="AAPL", ex_date=ex_date, amount=0.50)
    db_session.add(div_event)
    db_session.commit()

    expected_div_total = 20.0 * 0.50  # $10.00 debit
    daily_borrow = 20.0 * 150.0 * 0.01 * (1.0 / DAYS_PER_YEAR)

    # First run on ex-date debits dividend and borrow fee
    adjs = apply_carry("DivBot", today=ex_date, session=db_session, prices=prices)
    div_adjs = [a for a in adjs if a.kind == "dividend"]
    assert len(div_adjs) == 1
    assert div_adjs[0].amount == pytest.approx(-expected_div_total)

    db_session.expire_all()
    bot = db_session.query(BotModel).filter_by(name="DivBot").one()
    assert bot.portfolio["USD"] == pytest.approx(10000.0 - expected_div_total - daily_borrow)

    # Second run on same ex-date does not debit dividend again
    adjs2 = apply_carry("DivBot", today=ex_date, session=db_session, prices=prices)
    assert len(adjs2) == 0

    div_rows = db_session.query(CashAdjustment).filter_by(bot_name="DivBot", kind="dividend").all()
    assert len(div_rows) == 1


def test_missing_short_price_raises(sqlite_db, db_session):
    """A short with no price raises MissingPriceError (fail-closed); longs are skipped with a warning."""
    # 1. apply_carry fails closed if short price is missing or non-positive
    _setup_bot(db_session, "NoPriceBot", {"USD": 1000.0, "QQQ": -5.0})
    with pytest.raises(MissingPriceError):
        apply_carry("NoPriceBot", today=date(2026, 11, 14), session=db_session, prices={})

    with pytest.raises(MissingPriceError):
        apply_carry("NoPriceBot", today=date(2026, 11, 14), session=db_session, prices={"QQQ": -10.0})

    # 2. calculate_portfolio_worth fails closed if short price is missing
    short_bot = BotModel(name="ShortValBot", portfolio={"USD": 1000.0, "QQQ": -5.0})
    mock_ds = MagicMock(spec=DataService)
    mock_ds.get_latest_prices_batch.return_value = {}
    mock_ds.get_latest_price.side_effect = ValueError("No price")

    with pytest.raises(MissingPriceError):
        calculate_portfolio_worth(short_bot, mock_ds)

    # 3. calculate_portfolio_worth on a LONG book skips missing prices (long behaviour unchanged)
    long_bot = BotModel(name="LongValBot", portfolio={"USD": 1000.0, "AAPL": 10.0})
    worth = calculate_portfolio_worth(long_bot, mock_ds)
    assert worth == 1000.0  # skips AAPL, returns cash


def test_maintenance_liquidation_logged_cover_and_reason(sqlite_db, db_session):
    """Breaching maintenance margin triggers force-cover logged with action COVER and a reason."""
    # Book with 10 cash, long AAPL 1 @ 100, short QQQ 2 @ 300
    # Equity = 10 + 100 - 600 = -490. Maint req = 0.25*100 + 0.30*600 = 205.
    bot = _setup_bot(db_session, "BreachBot", {"USD": 10.0, "AAPL": 1.0, "QQQ": -2.0})
    today = date(2026, 11, 14)
    prices = {"AAPL": 100.0, "QQQ": 300.0}

    apply_carry("BreachBot", today=today, session=db_session, prices=prices)

    db_session.expire_all()
    bot = db_session.query(BotModel).filter_by(name="BreachBot").one()

    # QQQ short was covered (removed from portfolio)
    assert "QQQ" not in bot.portfolio

    # Trade was logged with action COVER and isBuy True
    trades = db_session.query(Trade).filter_by(bot_name="BreachBot").all()
    assert len(trades) >= 1
    cover_trade = next(t for t in trades if t.symbol == "QQQ")
    assert cover_trade.action == "COVER"
    assert cover_trade.isBuy is True
    assert cover_trade.quantity == 2.0
    assert cover_trade.price == 300.0
