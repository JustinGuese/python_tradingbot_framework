import pytest
from unittest.mock import MagicMock, patch
from tradingbot.livetrade.interactive_brokers import InteractiveBrokersBroker


def _resp(data):
    """ibind endpoints return a Result object exposing .data."""
    return MagicMock(data=data)


@pytest.fixture
def mock_client():
    """Patch the IbkrClient class so connect() yields a mock Web API client."""
    with patch('tradingbot.livetrade.interactive_brokers.IbkrClient') as cls:
        instance = cls.return_value
        instance.get_ledger.return_value = _resp({
            "USD": {"cashbalance": 100000.0, "netliquidationvalue": 150000.0}
        })
        instance.positions.return_value = _resp([
            {"ticker": "AAPL", "position": 10.0, "mktValue": 2000.0}
        ])
        instance.stock_conid_by_symbol.return_value = _resp({"AAPL": 265598})
        instance.place_order.return_value = _resp([{"order_id": "1"}])
        instance.live_orders.return_value = _resp({"orders": []})
        yield instance


def test_ib_get_cash(mock_client):
    broker = InteractiveBrokersBroker(account_id="DU123")
    assert broker.get_cash() == 100000.0
    mock_client.tickle.assert_called()


def test_ib_get_total_equity(mock_client):
    broker = InteractiveBrokersBroker(account_id="DU123")
    assert broker.get_total_equity() == 150000.0


def test_ib_get_total_equity_falls_back_to_positions_plus_cash(mock_client):
    # Ledger exposes cash but no net liquidation value.
    mock_client.get_ledger.return_value = _resp({"USD": {"cashbalance": 500.0}})
    broker = InteractiveBrokersBroker(account_id="DU123")
    # 2000.0 mktValue + 500.0 cash
    assert broker.get_total_equity() == 2500.0


def test_ib_get_positions(mock_client):
    broker = InteractiveBrokersBroker(account_id="DU123")
    assert broker.get_positions() == {"AAPL": 10.0}


def test_ib_get_positions_skips_zero_and_pages(mock_client):
    full_page = [{"ticker": f"SYM{i}", "position": 1.0} for i in range(100)]
    second_page = [
        {"ticker": "AAPL", "position": 5.0},
        {"ticker": "FLAT", "position": 0.0},  # closed position, must be dropped
    ]
    mock_client.positions.side_effect = [_resp(full_page), _resp(second_page)]

    broker = InteractiveBrokersBroker(account_id="DU123")
    positions = broker.get_positions()

    assert mock_client.positions.call_count == 2
    assert positions["AAPL"] == 5.0
    assert "FLAT" not in positions
    assert len(positions) == 101


def test_ib_place_order(mock_client):
    broker = InteractiveBrokersBroker(account_id="DU123")
    with patch.object(broker.symbol_mapper, 'unmap_symbol', return_value="AAPL"), \
         patch.object(broker, 'map_symbol', return_value={
             "symbol": "AAPL", "type": "stock", "sec_type": "STK",
             "exchange": "SMART", "currency": "USD",
         }):
        broker.place_order("AAPL", 10.5, "BUY")

    mock_client.place_order.assert_called_once()
    order = mock_client.place_order.call_args[0][0]
    assert order.conid == 265598
    assert order.side == "BUY"
    # 10.5 floored to a whole share
    assert order.quantity == 10.0
    assert order.order_type == "MKT"


def test_ib_place_order_skips_sub_one_quantity(mock_client):
    broker = InteractiveBrokersBroker(account_id="DU123")
    with patch.object(broker.symbol_mapper, 'unmap_symbol', return_value="VSNT"), \
         patch.object(broker, 'map_symbol', return_value={
             "symbol": "VSNT", "type": "stock", "sec_type": "STK",
             "exchange": "SMART", "currency": "USD",
         }):
        mock_client.stock_conid_by_symbol.return_value = _resp({"VSNT": 111})

        broker.place_order("VSNT", 2.16, "SELL")
        assert mock_client.place_order.call_args[0][0].quantity == 2.0

        mock_client.place_order.reset_mock()
        broker.place_order("VSNT", 0.4, "SELL")
        mock_client.place_order.assert_not_called()


def test_ib_place_order_skips_unresolvable_conid(mock_client):
    mock_client.stock_conid_by_symbol.return_value = _resp({})
    broker = InteractiveBrokersBroker(account_id="DU123")
    with patch.object(broker.symbol_mapper, 'unmap_symbol', return_value="NOPE"), \
         patch.object(broker, 'map_symbol', return_value={
             "symbol": "NOPE", "type": "stock", "sec_type": "STK",
             "exchange": "SMART", "currency": "USD",
         }):
        broker.place_order("NOPE", 5.0, "BUY")

    mock_client.place_order.assert_not_called()


def test_ib_place_order_rejects_non_equity(mock_client):
    """Forex/crypto/futures routing is not implemented over the Web API and must
    raise rather than silently mis-route."""
    broker = InteractiveBrokersBroker(account_id="DU123")
    with patch.object(broker.symbol_mapper, 'unmap_symbol', return_value="EURUSD=X"), \
         patch.object(broker, 'map_symbol', return_value={
             "symbol": "EURUSD", "type": "forex", "sec_type": "CASH",
             "exchange": "IDEALPRO", "currency": "USD",
         }):
        with pytest.raises(NotImplementedError):
            broker.place_order("EURUSD", 1000.0, "BUY")

    mock_client.place_order.assert_not_called()


def test_ib_cancel_open_orders(mock_client):
    mock_client.live_orders.return_value = _resp({"orders": [
        {"orderId": 1, "status": "Submitted"},
        {"orderId": 2, "status": "PreSubmitted"},
        {"orderId": 3, "status": "Filled"},     # terminal, must be skipped
        {"orderId": 4, "status": "Cancelled"},  # terminal, must be skipped
    ]})
    broker = InteractiveBrokersBroker(account_id="DU123")

    assert broker.cancel_open_orders() == 2
    assert mock_client.cancel_order.call_count == 2
    mock_client.cancel_order.assert_any_call("1", "DU123")
    mock_client.cancel_order.assert_any_call("2", "DU123")


def test_ib_map_symbol():
    broker = InteractiveBrokersBroker()
    with patch.object(broker.symbol_mapper, 'map_symbol', return_value={
        "symbol": "EURUSD",
        "type": "forex",
        "source": "default-rule",
    }):
        meta = broker.map_symbol("EURUSD=X")
        assert meta is not None
        assert meta["symbol"] == "EURUSD"
        assert meta["sec_type"] == "CASH"
        assert meta["exchange"] == "IDEALPRO"


def test_ib_map_symbol_defaults_to_us_equity():
    broker = InteractiveBrokersBroker()
    with patch.object(broker.symbol_mapper, 'map_symbol', return_value={
        "symbol": "QQQ",
        "type": "stock",
        "source": "default-rule",
    }):
        meta = broker.map_symbol("QQQ")
        assert meta is not None
        assert meta["sec_type"] == "STK"
        assert meta["exchange"] == "SMART"
        assert meta["currency"] == "USD"
