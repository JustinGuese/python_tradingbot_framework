from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from ib_async import LimitOrder

from tradingbot.livetrade.interactive_brokers import InteractiveBrokersBroker

STK_META = {"symbol": "AAPL", "type": "stock", "sec_type": "STK", "exchange": "SMART", "currency": "USD"}


def _val(tag, value, currency="USD", account="DU123"):
    return SimpleNamespace(tag=tag, value=str(value), currency=currency, account=account)


def _pos(symbol, qty, avg=10.0):
    c = SimpleNamespace(symbol=symbol)
    return SimpleNamespace(contract=c, position=qty, avgCost=avg)


@pytest.fixture
def broker():
    """A broker whose ib_async IB() is a mock that connects instantly."""
    with patch("tradingbot.livetrade.interactive_brokers.IB") as cls:
        ib = cls.return_value
        ib.managedAccounts.return_value = ["DU123"]
        ib.accountValues.return_value = [_val("TotalCashValue", 100000.0), _val("NetLiquidation", 150000.0)]
        ib.positions.return_value = []
        ib.openTrades.return_value = []
        b = InteractiveBrokersBroker(account_id="DU123", client_id=17)
        yield b


def _setup_place(broker, shortable=3.0, positions=None):
    if positions is not None:
        broker.ib.positions.return_value = positions
    ticker = MagicMock(shortable=shortable, ticks=[])
    broker.ib.reqMktData.return_value = ticker

    trade = MagicMock()
    trade.orderStatus.status = "Submitted"
    trade.log = []
    broker.ib.placeOrder.return_value = trade
    return trade


def test_ib_supports_short(broker):
    assert InteractiveBrokersBroker.supports_short is True
    assert broker.supports_short is True


def test_ib_short_builds_sell_on_empty_position(broker):
    # Positions empty -> SHORT builds a new contract and places a SELL order
    _setup_place(broker, shortable=3.0, positions=[])
    resolved_contract = MagicMock(name="resolved_contract")

    with (
        patch.object(broker.symbol_mapper, "unmap_symbol", return_value="AAPL"),
        patch.object(broker, "map_symbol", return_value=STK_META),
        patch.object(broker, "_resolve_stk_contract", return_value=resolved_contract),
    ):
        status = broker.place_order("AAPL", 10.0, side="SHORT")

    assert status == "Submitted"
    contract, order = broker.ib.placeOrder.call_args[0]
    assert contract is resolved_contract
    assert order.action == "SELL"
    assert order.totalQuantity == 10.0
    assert order.account == "DU123"


def test_ib_cover_buys_at_most_open_short(broker):
    # Short position of -10 shares held. COVER 15 shares should cap at 10.
    held = _pos("AAPL", -10.0)
    _setup_place(broker, positions=[held])

    with (
        patch.object(broker.symbol_mapper, "unmap_symbol", return_value="AAPL"),
        patch.object(broker, "map_symbol", return_value=STK_META),
    ):
        status = broker.place_order("AAPL", 15.0, side="COVER")

    assert status == "Submitted"
    contract, order = broker.ib.placeOrder.call_args[0]
    assert contract is held.contract
    assert order.action == "BUY"
    assert order.totalQuantity == 10.0

    # Partial cover: requested 4 shares of 10 open short -> buys 4
    broker.ib.placeOrder.reset_mock()
    with (
        patch.object(broker.symbol_mapper, "unmap_symbol", return_value="AAPL"),
        patch.object(broker, "map_symbol", return_value=STK_META),
    ):
        status = broker.place_order("AAPL", 4.0, side="COVER")

    contract, order = broker.ib.placeOrder.call_args[0]
    assert contract is held.contract
    assert order.action == "BUY"
    assert order.totalQuantity == 4.0


def test_ib_cover_skips_when_no_open_short(broker):
    # No short position (empty or long only)
    _setup_place(broker, positions=[_pos("AAPL", 10.0)])

    with (
        patch.object(broker.symbol_mapper, "unmap_symbol", return_value="AAPL"),
        patch.object(broker, "map_symbol", return_value=STK_META),
    ):
        res = broker.place_order("AAPL", 5.0, side="COVER")

    assert res is None
    broker.ib.placeOrder.assert_not_called()


def test_ib_unshortable_tick_skips_order(broker):
    # Tick 46 <= 2.5 means unshortable -> skip without raising
    _setup_place(broker, shortable=2.0, positions=[])

    with (
        patch.object(broker.symbol_mapper, "unmap_symbol", return_value="AAPL"),
        patch.object(broker, "map_symbol", return_value=STK_META),
        patch.object(broker, "_resolve_stk_contract", return_value=MagicMock()),
    ):
        res = broker.place_order("AAPL", 10.0, side="SHORT")

    assert res is None
    broker.ib.placeOrder.assert_not_called()


def test_ib_shortable_tick_missing_skips_order(broker):
    # Tick missing (None) -> skip with warning/info log, don't raise
    _setup_place(broker, shortable=None, positions=[])

    with (
        patch.object(broker.symbol_mapper, "unmap_symbol", return_value="AAPL"),
        patch.object(broker, "map_symbol", return_value=STK_META),
        patch.object(broker, "_resolve_stk_contract", return_value=MagicMock()),
        patch.object(broker, "_is_shortable", return_value=False),
    ):
        res = broker.place_order("AAPL", 10.0, side="SHORT")

    assert res is None
    broker.ib.placeOrder.assert_not_called()


def test_ib_fractional_qty_floors_for_short_and_cover(broker):
    # SHORT floors fractional shares
    _setup_place(broker, shortable=3.0, positions=[])
    with (
        patch.object(broker.symbol_mapper, "unmap_symbol", return_value="AAPL"),
        patch.object(broker, "map_symbol", return_value=STK_META),
        patch.object(broker, "_resolve_stk_contract", return_value=MagicMock()),
    ):
        broker.place_order("AAPL", 10.8, side="SHORT")

    _, order = broker.ib.placeOrder.call_args[0]
    assert order.totalQuantity == 10.0

    # SHORT sub-one share skipped
    broker.ib.placeOrder.reset_mock()
    with (
        patch.object(broker.symbol_mapper, "unmap_symbol", return_value="AAPL"),
        patch.object(broker, "map_symbol", return_value=STK_META),
        patch.object(broker, "_resolve_stk_contract", return_value=MagicMock()),
    ):
        res = broker.place_order("AAPL", 0.7, side="SHORT")

    assert res is None
    broker.ib.placeOrder.assert_not_called()

    # COVER floors fractional shares
    held = _pos("AAPL", -10.0)
    broker.ib.positions.return_value = [held]
    with (
        patch.object(broker.symbol_mapper, "unmap_symbol", return_value="AAPL"),
        patch.object(broker, "map_symbol", return_value=STK_META),
    ):
        broker.place_order("AAPL", 5.9, side="COVER")

    _, order = broker.ib.placeOrder.call_args[0]
    assert order.totalQuantity == 5.0

    # COVER sub-one share skipped
    broker.ib.placeOrder.reset_mock()
    with (
        patch.object(broker.symbol_mapper, "unmap_symbol", return_value="AAPL"),
        patch.object(broker, "map_symbol", return_value=STK_META),
    ):
        res = broker.place_order("AAPL", 0.4, side="COVER")

    assert res is None
    broker.ib.placeOrder.assert_not_called()


def test_ib_short_and_cover_limit_orders(broker):
    _setup_place(broker, shortable=3.0, positions=[_pos("AAPL", -10.0)])
    with (
        patch.object(broker.symbol_mapper, "unmap_symbol", return_value="AAPL"),
        patch.object(broker, "map_symbol", return_value=STK_META),
        patch.object(broker, "_resolve_stk_contract", return_value=MagicMock()),
    ):
        broker.place_order("AAPL", 5.0, side="SHORT", limit_price=150.50)
        _, order_short = broker.ib.placeOrder.call_args[0]
        assert isinstance(order_short, LimitOrder)
        assert order_short.action == "SELL"
        assert order_short.lmtPrice == 150.50
        assert order_short.totalQuantity == 5.0

        broker.place_order("AAPL", 5.0, side="COVER", limit_price=145.25)
        _, order_cover = broker.ib.placeOrder.call_args[0]
        assert isinstance(order_cover, LimitOrder)
        assert order_cover.action == "BUY"
        assert order_cover.lmtPrice == 145.25
        assert order_cover.totalQuantity == 5.0
