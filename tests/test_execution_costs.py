"""
Tests for the live execution cost model and the relative no-trade band.

The bug these pin: PortfolioManager filled every paper trade at the raw mid
price with no slippage or commission, while utils/backtest.py has modelled 5bps
slippage since forever — so paper trading was systematically MORE optimistic
than the backtest of the same strategy, which flattered exactly the high-churn
bots. And the no-trade band was a flat $1 regardless of book size, so
RegimeAdaptiveBot put 93% of 1123 trades through at under $25 notional (the
smallest was $0.011).
"""

from unittest.mock import MagicMock

import pytest

from tradingbot.utils.bot_repository import BotRepository
from tradingbot.utils.config import PORTFOLIO_CONFIG, ExecutionConfig
from tradingbot.utils.db import Trade
from tradingbot.utils.portfolio_manager import PortfolioManager, should_trade

# Deliberately non-zero on both axes so buy/sell arithmetic is distinguishable.
COSTLY = ExecutionConfig(slippage_pct=0.01, commission_pct=0.001, min_trade_usd=25.0, rebalance_band_pct=0.05)
FREE = ExecutionConfig(slippage_pct=0.0, commission_pct=0.0, min_trade_usd=1.0, rebalance_band_pct=0.0)


def _pm(bot, price, cfg):
    ds = MagicMock()
    ds.get_latest_price.return_value = price
    return PortfolioManager(bot, bot.name, ds, BotRepository, execution_config=cfg)


# ------------------------------------------------------------------
# Buy arithmetic
# ------------------------------------------------------------------


def test_buy_debits_full_gross_budget(db_session, test_bot):
    """Commission comes out of the budget, never on top of it."""
    _pm(test_bot, 100.0, COSTLY).buy("AAPL", quantity_usd=1000.0, session=db_session)
    assert test_bot.portfolio["USD"] == 9000.0


def test_buy_applies_slippage_and_commission(db_session, test_bot):
    _pm(test_bot, 100.0, COSTLY).buy("AAPL", quantity_usd=1000.0, session=db_session)
    expected = (1000.0 - 1000.0 * COSTLY.commission_pct) / (100.0 * (1 + COSTLY.slippage_pct))
    assert test_bot.portfolio["AAPL"] == pytest.approx(expected)


def test_buy_all_cash_leaves_exactly_zero_usd(db_session, test_bot):
    """
    The overdraw guard, covering the ~20 single-asset bots that buy with the
    default quantity_usd=-1. Because commission is inside the budget, USD lands
    on exactly 0.0 at any commission rate.
    """
    _pm(test_bot, 137.42, COSTLY).buy("AAPL", session=db_session)
    assert test_bot.portfolio["USD"] == 0.0
    assert test_bot.portfolio["AAPL"] > 0


def test_buy_reconciles_against_cash_removed(db_session, test_bot):
    """quantity * execution_price + commission must equal the cash that left."""
    _pm(test_bot, 100.0, COSTLY).buy("AAPL", quantity_usd=1000.0, session=db_session)
    trade = db_session.query(Trade).filter_by(symbol="AAPL").one()
    commission = 1000.0 * COSTLY.commission_pct
    assert trade.quantity * trade.price + commission == pytest.approx(1000.0)


def test_buy_at_non_positive_price_is_skipped(db_session, test_bot):
    """A ZeroDivisionError here would abort a rebalance between its sells and buys."""
    _pm(test_bot, 0.0, COSTLY).buy("AAPL", quantity_usd=1000.0, session=db_session)
    assert test_bot.portfolio["USD"] == 10000.0
    assert "AAPL" not in test_bot.portfolio


# ------------------------------------------------------------------
# Sell arithmetic
# ------------------------------------------------------------------


@pytest.mark.parametrize("slippage", [0.0, 0.0005, 0.01])
def test_sell_share_count_uses_reference_price(db_session, test_bot, slippage):
    """
    Regression guard for the partial-sell denomination decision: quantity_usd is
    position notional at the REFERENCE price, so the share count is invariant to
    slippage. Sizing it off the execution price instead would shed slightly too
    many shares, undershoot the target, and leave the next rebalance a fresh
    diff to correct -- manufacturing the churn this change removes.
    """
    test_bot.portfolio = {"USD": 0.0, "AAPL": 10.0}
    BotRepository.update_bot(test_bot, session=db_session)

    cfg = ExecutionConfig(slippage_pct=slippage, commission_pct=0.0)
    _pm(test_bot, 150.0, cfg).sell("AAPL", quantity_usd=750.0, session=db_session)

    assert test_bot.portfolio["AAPL"] == pytest.approx(5.0)


def test_sell_credits_net_of_slippage_and_commission(db_session, test_bot):
    test_bot.portfolio = {"USD": 0.0, "AAPL": 10.0}
    BotRepository.update_bot(test_bot, session=db_session)

    _pm(test_bot, 150.0, COSTLY).sell("AAPL", quantity_usd=750.0, session=db_session)

    gross = 5.0 * 150.0 * (1 - COSTLY.slippage_pct)
    assert test_bot.portfolio["USD"] == pytest.approx(gross * (1 - COSTLY.commission_pct))


def test_sell_all_removes_the_symbol(db_session, test_bot):
    test_bot.portfolio = {"USD": 0.0, "AAPL": 10.0}
    BotRepository.update_bot(test_bot, session=db_session)

    _pm(test_bot, 150.0, COSTLY).sell("AAPL", session=db_session)

    assert "AAPL" not in test_bot.portfolio
    assert test_bot.portfolio["USD"] > 0


def test_sell_more_than_held_clamps_and_prices_after_the_clamp(db_session, test_bot):
    test_bot.portfolio = {"USD": 0.0, "AAPL": 10.0}
    BotRepository.update_bot(test_bot, session=db_session)

    # Ask for 20 shares' worth; only 10 are held.
    _pm(test_bot, 150.0, COSTLY).sell("AAPL", quantity_usd=3000.0, session=db_session)

    assert "AAPL" not in test_bot.portfolio
    expected = 10.0 * 150.0 * (1 - COSTLY.slippage_pct) * (1 - COSTLY.commission_pct)
    assert test_bot.portfolio["USD"] == pytest.approx(expected)


def test_sell_logs_execution_price_and_net_proceeds(db_session, test_bot):
    test_bot.portfolio = {"USD": 0.0, "AAPL": 10.0}
    BotRepository.update_bot(test_bot, session=db_session)

    _pm(test_bot, 150.0, COSTLY).sell("AAPL", session=db_session)

    trade = db_session.query(Trade).filter_by(symbol="AAPL").one()
    assert trade.price == pytest.approx(150.0 * (1 - COSTLY.slippage_pct))
    # `profit` is net cash proceeds, not P&L. Commission is recoverable from it.
    assert trade.quantity * trade.price - trade.profit == pytest.approx(
        trade.quantity * trade.price * COSTLY.commission_pct
    )


# ------------------------------------------------------------------
# Rollback lever
# ------------------------------------------------------------------


def test_zero_cost_config_reproduces_legacy_numbers(db_session, test_bot):
    """
    Executable proof that setting the four env vars to 0/0/1/0 restores the
    pre-change behaviour, which is the production rollback path.
    """
    _pm(test_bot, 100.0, FREE).buy("AAPL", quantity_usd=1000.0, session=db_session)
    assert test_bot.portfolio["AAPL"] == 10.0
    assert test_bot.portfolio["USD"] == 9000.0

    _pm(test_bot, 150.0, FREE).sell("AAPL", quantity_usd=750.0, session=db_session)
    assert test_bot.portfolio["USD"] == 9750.0


# ------------------------------------------------------------------
# The no-trade band
# ------------------------------------------------------------------


def test_band_blocks_an_adjustment_below_the_floor():
    assert not should_trade(5.0, 164.0, COSTLY)


def test_band_allows_an_adjustment_exactly_at_the_floor():
    assert should_trade(25.0, 164.0, COSTLY)


def test_band_scales_with_position_size():
    """At a $5,000 position the percentage term takes over from the $25 floor."""
    assert COSTLY.no_trade_threshold(5000.0) == 250.0
    assert not should_trade(100.0, 5000.0, COSTLY)
    assert should_trade(300.0, 5000.0, COSTLY)


def test_full_exit_bypasses_the_band():
    """Otherwise a sub-band position could never be liquidated, and the
    min_asset_value_usd filter (which works by making a symbol a full exit)
    would become a no-op for exactly the positions it exists to clear."""
    assert should_trade(-3.0, 3.0, COSTLY, is_full_exit=True)


def test_full_exit_of_float_dust_does_not_trade():
    assert not should_trade(-1e-9, 1e-9, COSTLY, is_full_exit=True)


def test_new_entry_does_not_bypass_the_band():
    """Easy to exit, hard to enter, so position count trends down."""
    assert not should_trade(10.0, 10.0, COSTLY)


def test_trade_floor_is_at_or_below_the_position_floor():
    """
    If the trade floor exceeded the position floor, rebalance_portfolio could
    approve a target position it is then forbidden to open. This invariant is
    what guarantees the band can never block an entry the only_over_50_usd
    filter has already approved.
    """
    assert COSTLY.min_trade_usd <= PORTFOLIO_CONFIG.min_asset_value_usd


# ------------------------------------------------------------------
# Config hardening
# ------------------------------------------------------------------


def test_execution_config_reads_env(monkeypatch):
    monkeypatch.setenv("EXECUTION_SLIPPAGE_PCT", "0.002")
    monkeypatch.setenv("EXECUTION_MIN_TRADE_USD", "40")
    cfg = ExecutionConfig.from_env()
    assert cfg.slippage_pct == 0.002
    assert cfg.min_trade_usd == 40.0


@pytest.mark.parametrize("bad", ["abc", "99", "-1", ""])
def test_execution_config_survives_garbage_env(monkeypatch, bad):
    """
    config.py is imported at process start by all 28 bot CronJobs and builds
    EXECUTION_CONFIG at import time, so one typo'd env var must not be able to
    take the whole fleet down simultaneously.
    """
    monkeypatch.setenv("EXECUTION_SLIPPAGE_PCT", bad)
    assert ExecutionConfig.from_env().slippage_pct == ExecutionConfig().slippage_pct


# ------------------------------------------------------------------
# Band behaviour through a real rebalance
# ------------------------------------------------------------------


def _rebalance_pm(bot, prices, cfg):
    ds = MagicMock()
    ds.get_latest_prices_batch.return_value = prices
    ds.get_latest_price.side_effect = lambda sym, cached=None: prices[sym]
    return PortfolioManager(bot, bot.name, ds, BotRepository, execution_config=cfg)


def test_rebalance_skips_adjustments_inside_the_band(sqlite_db, db_session, test_bot):
    """The headline fix: crumb trades stop being emitted."""
    test_bot.portfolio = {"USD": 0.0, "AAPL": 50.0, "GOOG": 25.0}
    BotRepository.update_bot(test_bot, session=db_session)
    db_session.commit()

    # Book is $10,000; targets are $5,010 / $4,990 — a $10 drift on each leg,
    # under the $25 floor.
    pm = _rebalance_pm(test_bot, {"AAPL": 100.0, "GOOG": 200.0}, COSTLY)
    pm.rebalance_portfolio({"AAPL": 0.501, "GOOG": 0.499})

    assert db_session.query(Trade).count() == 0


def test_rebalance_liquidates_a_dust_position_not_in_the_target(sqlite_db, db_session, test_bot):
    """
    Critical regression test for the full-exit bypass. A $3 holding is far below
    the $25 floor; if full exits were banded it could never be sold and sub-band
    positions would accumulate forever with nothing to alert on.
    """
    test_bot.portfolio = {"USD": 0.0, "AAPL": 99.7, "ZZZZ": 3.0}
    BotRepository.update_bot(test_bot, session=db_session)
    db_session.commit()

    pm = _rebalance_pm(test_bot, {"AAPL": 100.0, "ZZZZ": 1.0}, COSTLY)
    pm.rebalance_portfolio({"AAPL": 1.0})

    db_session.expire_all()
    updated = db_session.query(type(test_bot)).filter_by(name=test_bot.name).one()
    assert "ZZZZ" not in updated.portfolio


def test_rebalance_never_leaves_negative_usd(sqlite_db, db_session, test_bot):
    test_bot.portfolio = {"USD": 0.0, "AAPL": 50.0, "GOOG": 25.0}
    BotRepository.update_bot(test_bot, session=db_session)
    db_session.commit()

    pm = _rebalance_pm(test_bot, {"AAPL": 100.0, "GOOG": 200.0}, COSTLY)
    pm.rebalance_portfolio({"AAPL": 0.2, "GOOG": 0.8})

    db_session.expire_all()
    updated = db_session.query(type(test_bot)).filter_by(name=test_bot.name).one()
    assert updated.portfolio.get("USD", 0) >= -1e-9
