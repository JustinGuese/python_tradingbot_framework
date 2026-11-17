from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from tradingbot.livetrade.broker import LiveBroker
from tradingbot.livetrade.copier import LiveTradeCopier
from tradingbot.livetrade.execution import ExecutionPolicy, marketable_limit
from tradingbot.utils.db import Bot

# ----------------------------------------------------------------------
# 1. Window math tests (winter, summer, early close, weekend/holiday)
# ----------------------------------------------------------------------


def test_window_math_winter_session():
    """Winter (EST, UTC-5): Open 14:30 UTC, Close 21:00 UTC. Window [15:00, 20:45]."""
    policy = ExecutionPolicy(min_after_open_minutes=30, min_before_close_minutes=15)

    before_open = datetime(2026, 1, 14, 14, 59, tzinfo=UTC)
    window_start = datetime(2026, 1, 14, 15, 0, tzinfo=UTC)
    mid_session = datetime(2026, 1, 14, 15, 5, tzinfo=UTC)
    window_end = datetime(2026, 1, 14, 20, 45, tzinfo=UTC)
    after_close = datetime(2026, 1, 14, 20, 46, tzinfo=UTC)

    assert not policy.in_window(before_open)
    assert policy.in_window(window_start)
    assert policy.in_window(mid_session)
    assert policy.in_window(window_end)
    assert not policy.in_window(after_close)


def test_window_math_summer_session():
    """Summer (EDT, UTC-4): Open 13:30 UTC, Close 20:00 UTC. Window [14:00, 19:45]."""
    policy = ExecutionPolicy(min_after_open_minutes=30, min_before_close_minutes=15)

    before_open = datetime(2026, 6, 17, 13, 59, tzinfo=UTC)
    window_start = datetime(2026, 6, 17, 14, 0, tzinfo=UTC)
    mid_session = datetime(2026, 6, 17, 15, 5, tzinfo=UTC)
    window_end = datetime(2026, 6, 17, 19, 45, tzinfo=UTC)
    after_close = datetime(2026, 6, 17, 19, 46, tzinfo=UTC)

    assert not policy.in_window(before_open)
    assert policy.in_window(window_start)
    assert policy.in_window(mid_session)
    assert policy.in_window(window_end)
    assert not policy.in_window(after_close)


def test_window_math_early_close():
    """Day after Thanksgiving (13:00 EST close = 18:00 UTC). Window [15:00, 17:45]."""
    policy = ExecutionPolicy(min_after_open_minutes=30, min_before_close_minutes=15)

    mid_session = datetime(2026, 11, 27, 15, 5, tzinfo=UTC)
    window_end = datetime(2026, 11, 27, 17, 45, tzinfo=UTC)
    after_window = datetime(2026, 11, 27, 17, 46, tzinfo=UTC)

    assert policy.in_window(mid_session)
    assert policy.in_window(window_end)
    assert not policy.in_window(after_window)


def test_window_math_weekend_and_holiday():
    """Weekends and market holidays must refuse submission."""
    policy = ExecutionPolicy()

    sunday = datetime(2026, 11, 15, 15, 5, tzinfo=UTC)
    thanksgiving = datetime(2026, 11, 26, 15, 5, tzinfo=UTC)

    assert not policy.in_window(sunday)
    assert not policy.in_window(thanksgiving)


# ----------------------------------------------------------------------
# 2. Limit price sides, bands, and edge cases
# ----------------------------------------------------------------------


def test_marketable_limit_sides_and_bands():
    """BUY adds limit band (+0.15%), SELL subtracts limit band (-0.15%)."""
    assert marketable_limit("BUY", 100.0) == 100.15
    assert marketable_limit("SELL", 100.0) == 99.85
    # Case insensitivity
    assert marketable_limit("buy", 100.0) == 100.15
    assert marketable_limit("sell", 100.0) == 99.85
    # Custom band override
    assert marketable_limit("BUY", 100.0, band_pct=0.01) == 101.0
    assert marketable_limit("SELL", 100.0, band_pct=0.01) == 99.0


def test_marketable_limit_invalid_inputs():
    """Negative prices or invalid sides must raise ValueError."""
    with pytest.raises(ValueError, match="positive"):
        marketable_limit("BUY", 0.0)
    with pytest.raises(ValueError, match="positive"):
        marketable_limit("BUY", -10.0)
    with pytest.raises(ValueError, match="Invalid side"):
        marketable_limit("HOLD", 100.0)


# ----------------------------------------------------------------------
# 3. Copier window guard and limit order execution
# ----------------------------------------------------------------------


def _setup_mock_copier(always_open: bool = False, supports_limit_orders: bool = False):
    broker = MagicMock(spec=LiveBroker)
    broker.name = "test_broker"
    broker.always_open = always_open
    broker.supports_limit_orders = supports_limit_orders
    broker.get_cash.return_value = 100_000.0
    broker.get_total_equity.return_value = 10_000.0
    broker.get_positions.return_value = {}
    broker.cancel_open_orders.return_value = 0
    broker.is_tradeable.return_value = True
    broker.map_symbol.side_effect = lambda s: {"symbol": s, "type": "stock"}
    broker.get_latest_price.return_value = 100.0

    mock_bot = MagicMock(spec=Bot)
    mock_bot.portfolio = {"USD": 0, "AAPL": 10}

    bot_repo = MagicMock()
    bot_repo.read_portfolio.return_value = mock_bot.portfolio

    data_service = MagicMock()
    data_service.get_latest_prices_batch.return_value = {"AAPL": 100.0}

    policy = MagicMock(spec=ExecutionPolicy)

    copier = LiveTradeCopier(
        broker=broker,
        bot_weights={"bot1": 1.0},
        dry_run=False,
        execution_policy=policy,
    )
    copier.cash_proxy = None
    copier.bot_repo = bot_repo
    copier.data_service = data_service
    return copier, broker, policy


def test_copier_skips_outside_window(caplog):
    """Copier aborts sync with ERROR log and executes no orders when outside window."""
    copier, broker, policy = _setup_mock_copier(always_open=False, supports_limit_orders=False)
    policy.explain_window.return_value = (False, "outside US trading hours")

    with caplog.at_level("ERROR"):
        copier.sync()

    assert any("outside US execution window" in r.message for r in caplog.records)
    broker.place_order.assert_not_called()


def test_copier_does_not_skip_for_always_open_broker():
    """Brokers with always_open=True (e.g. Hyperliquid) ignore the US window guard."""
    copier, broker, policy = _setup_mock_copier(always_open=True, supports_limit_orders=False)
    policy.explain_window.return_value = (False, "outside US trading hours")

    copier.sync()

    broker.place_order.assert_called_once()
    assert broker.place_order.call_args[0][0] == "AAPL"
    assert broker.place_order.call_args[0][2] == "BUY"


def test_copier_passes_limit_price_only_to_capable_brokers():
    """Copier passes limit_price when supports_limit_orders=True, but omits it when False."""
    # 1. Capable broker: receives limit_price
    copier_capable, broker_capable, policy_capable = _setup_mock_copier(always_open=False, supports_limit_orders=True)
    policy_capable.explain_window.return_value = (True, "in window")
    policy_capable.marketable_limit.return_value = 100.15

    copier_capable.sync()
    broker_capable.place_order.assert_called_once()
    _, kwargs = broker_capable.place_order.call_args
    assert kwargs.get("limit_price") == 100.15

    # 2. Incompatible broker: does NOT receive limit_price
    copier_incapable, broker_incapable, policy_incapable = _setup_mock_copier(
        always_open=False, supports_limit_orders=False
    )
    policy_incapable.explain_window.return_value = (True, "in window")

    copier_incapable.sync()
    broker_incapable.place_order.assert_called_once()
    _, kwargs_incapable = broker_incapable.place_order.call_args
    assert "limit_price" not in kwargs_incapable
