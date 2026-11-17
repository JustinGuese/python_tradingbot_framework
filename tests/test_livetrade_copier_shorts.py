import logging
from unittest.mock import MagicMock

from tradingbot.livetrade.broker import LiveBroker
from tradingbot.livetrade.copier import LiveTradeCopier
from tradingbot.utils.db import Bot


def _make_copier(supports_short: bool = False, dry_run: bool = False):
    broker = MagicMock(spec=LiveBroker)
    broker.name = "test_broker"
    broker.supports_short = supports_short
    broker.supports_limit_orders = False
    broker.get_latest_price.return_value = 100.0
    broker.get_cash.return_value = 100_000.0

    copier = LiveTradeCopier(broker=broker, bot_weights={"bot1": 1.0}, dry_run=dry_run)
    copier.bot_repo = MagicMock()
    copier.data_service = MagicMock()
    return copier, broker


def test_short_weight_dropped_for_non_short_broker(caplog):
    # Broker does NOT support short
    copier, broker = _make_copier(supports_short=False)

    mock_bot = MagicMock(spec=Bot)
    mock_bot.portfolio = {"USD": 1000.0, "AAPL": -10.0}
    copier.bot_repo.read_portfolio.return_value = mock_bot.portfolio
    copier.data_service.get_latest_prices_batch.return_value = {"AAPL": 50.0}

    with caplog.at_level(logging.INFO):
        weights = copier._calculate_target_weights()

    # Short weight should be dropped
    assert "AAPL" not in weights
    assert any("short dropped" in record.message for record in caplog.records)
    assert not any("option structure" in record.message for record in caplog.records)

    # When broker DOES support short, negative weight is kept
    broker.supports_short = True
    weights_supported = copier._calculate_target_weights()
    assert "AAPL" in weights_supported
    assert weights_supported["AAPL"] < 0


def test_supported_broker_gets_short_then_cover_on_flip():
    copier, broker = _make_copier(supports_short=True, dry_run=False)
    broker.get_latest_price.return_value = 200.0

    # Step 1: Target is short (-0.2), initial position is flat (0.0) -> SHORT
    target_weights = {"QQQ": {"weight": -0.2, "type": "stock"}}
    current_positions = {"QQQ": 0.0}
    total_equity = 10_000.0  # target value = -$2000 => -10 shares

    orders1 = copier._calculate_orders(target_weights, current_positions, total_equity)
    assert len(orders1) == 1
    assert orders1[0]["symbol"] == "QQQ"
    assert orders1[0]["side"] == "SHORT"
    assert orders1[0]["quantity"] == 10.0

    copier._execute_orders(orders1)
    broker.place_order.assert_called_with("QQQ", 10.0, "SHORT", symbol_type="stock")

    # Step 2: Target flips to flat (0.0), current position is short (-10.0) -> COVER
    broker.place_order.reset_mock()
    target_weights_flip = {"QQQ": {"weight": 0.0, "type": "stock"}}
    current_positions_flip = {"QQQ": -10.0}

    orders2 = copier._calculate_orders(target_weights_flip, current_positions_flip, total_equity)
    assert len(orders2) == 1
    assert orders2[0]["symbol"] == "QQQ"
    assert orders2[0]["side"] == "COVER"
    assert orders2[0]["quantity"] == 10.0

    copier._execute_orders(orders2)
    broker.place_order.assert_called_with("QQQ", 10.0, "COVER", symbol_type="stock")

    # Step 3: Flip across zero in a single calculation (short -10.0 to long +10.0)
    broker.place_order.reset_mock()
    target_weights_long = {"QQQ": {"weight": 0.2, "type": "stock"}}
    orders3 = copier._calculate_orders(target_weights_long, current_positions_flip, total_equity)
    assert len(orders3) == 2
    assert orders3[0]["side"] == "COVER"
    assert orders3[0]["quantity"] == 10.0
    assert orders3[1]["side"] == "BUY"
    assert orders3[1]["quantity"] == 10.0


def test_cash_clamp_never_scales_a_cover():
    copier, broker = _make_copier(supports_short=True, dry_run=False)
    # Available cash is low ($1000)
    broker.get_cash.return_value = 1000.0

    orders = [
        {"symbol": "QQQ", "quantity": 10.0, "side": "COVER", "value": 2000.0, "type": "stock"},
        {"symbol": "SPY", "quantity": 10.0, "side": "BUY", "value": 5000.0, "type": "stock"},
    ]

    copier._execute_orders(orders)

    # QQQ COVER must be placed with full quantity (10.0), unscaled by cash clamp
    broker.place_order.assert_any_call("QQQ", 10.0, "COVER", symbol_type="stock")

    # SPY BUY must be scaled down by cash clamp (budget = 980, scale = 980/5000 = 0.196 => qty 1.96)
    spy_call = next(c for c in broker.place_order.call_args_list if c[0][0] == "SPY")
    assert spy_call[0][1] < 10.0
    assert spy_call[0][2] == "BUY"

    # Even when available cash is 0, COVER executes unscaled while BUY is skipped
    broker.place_order.reset_mock()
    broker.get_cash.return_value = 0.0

    orders_zero_cash = [
        {"symbol": "QQQ", "quantity": 10.0, "side": "COVER", "value": 2000.0, "type": "stock"},
        {"symbol": "SPY", "quantity": 10.0, "side": "BUY", "value": 5000.0, "type": "stock"},
    ]
    copier._execute_orders(orders_zero_cash)
    broker.place_order.assert_called_once_with("QQQ", 10.0, "COVER", symbol_type="stock")


def test_stray_short_covered_once_not_doubled(caplog):
    # Supported broker covers stray short once with side=COVER, not SELL
    copier_supp, broker_supp = _make_copier(supports_short=True, dry_run=False)
    current_positions = {"AAPL": -5.0}

    orders = copier_supp._calculate_orders({}, current_positions, 10_000.0)
    assert len(orders) == 1
    assert orders[0]["symbol"] == "AAPL"
    assert orders[0]["side"] == "COVER"
    assert orders[0]["quantity"] == 5.0

    copier_supp._execute_orders(orders)
    broker_supp.place_order.assert_called_once_with("AAPL", 5.0, "COVER", symbol_type="stock")

    # Non-short broker aborts the symbol entirely (never places SELL or doubles)
    copier_non, broker_non = _make_copier(supports_short=False, dry_run=False)
    with caplog.at_level(logging.ERROR):
        orders_non = copier_non._calculate_orders({}, current_positions, 10_000.0)

    assert orders_non == []
    assert any("Unexpected short position" in record.message for record in caplog.records)
    copier_non._execute_orders(orders_non)
    broker_non.place_order.assert_not_called()


def test_park_idle_cash_uses_gross_weights():
    copier, broker = _make_copier(supports_short=True)
    copier.cash_proxy = "SHV"
    copier.cash_buffer = 0.02
    broker.is_tradeable.return_value = True
    broker.map_symbol.return_value = {"symbol": "SHV", "type": "stock"}

    # Long 0.5 and Short -0.3: gross = 0.8, idle = 1.0 - 0.8 - 0.02 = 0.18
    # (Net would have been 0.2, giving idle = 0.78, which is wrong)
    target_weights = {
        "AAPL": {"weight": 0.5, "type": "stock"},
        "QQQ": {"weight": -0.3, "type": "stock"},
    }

    copier._park_idle_cash(target_weights)
    assert "SHV" in target_weights
    assert round(target_weights["SHV"]["weight"], 4) == 0.18
