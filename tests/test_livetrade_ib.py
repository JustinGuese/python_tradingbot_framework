import pytest
from unittest.mock import MagicMock, patch
from tradingbot.livetrade.interactive_brokers import InteractiveBrokersBroker

@pytest.fixture
def mock_ib():
    with patch('tradingbot.livetrade.interactive_brokers.IB') as mock:
        instance = mock.return_value
        instance.accountSummary.return_value = [
            MagicMock(tag="TotalCashValue", currency="USD", value="100000.0"),
            MagicMock(tag="NetLiquidation", currency="USD", value="150000.0")
        ]
        instance.positions.return_value = [
            MagicMock(contract=MagicMock(symbol="AAPL"), position=10.0)
        ]
        instance.client_id = 17 # Default in InteractiveBrokersBroker
        yield instance

def test_ib_get_cash(mock_ib):
    broker = InteractiveBrokersBroker(account_id="DU123")
    cash = broker.get_cash()
    assert cash == 100000.0
    mock_ib.connect.assert_called()

def test_ib_get_total_equity(mock_ib):
    broker = InteractiveBrokersBroker(account_id="DU123")
    equity = broker.get_total_equity()
    assert equity == 150000.0

def test_ib_get_positions(mock_ib):
    broker = InteractiveBrokersBroker(account_id="DU123")
    positions = broker.get_positions()
    assert positions == {"AAPL": 10.0}

def test_ib_place_order(mock_ib):
    broker = InteractiveBrokersBroker(account_id="DU123")
    # Mock map_symbol to return something valid
    with patch.object(broker, 'map_symbol', return_value={
        "broker_symbol": "AAPL",
        "sec_type": "STK",
        "exchange": "SMART",
        "currency": "USD"
    }):
        mock_trade = MagicMock()
        mock_trade.orderStatus.status = 'PreSubmitted'
        mock_ib.placeOrder.return_value = mock_trade
        
        broker.place_order("AAPL", 10.5, "BUY")
        
        mock_ib.placeOrder.assert_called()
        args, _ = mock_ib.placeOrder.call_args
        contract = args[0]
        order = args[1]
        assert contract.symbol == "AAPL"
        assert order.action == "BUY"
        # Should be floored to 10
        assert order.totalQuantity == 10.0

def test_ib_place_order_floors_fractional_stock(mock_ib):
    broker = InteractiveBrokersBroker(account_id="DU123")
    with patch.object(broker, 'map_symbol', return_value={
        "broker_symbol": "VSNT",
        "sec_type": "STK",
        "exchange": "SMART",
        "currency": "USD"
    }):
        # Case 1: 2.16 -> 2.0
        broker.place_order("VSNT", 2.16, "SELL")
        args, _ = mock_ib.placeOrder.call_args
        assert args[1].totalQuantity == 2.0

        # Case 2: 0.4 -> Skipped
        mock_ib.placeOrder.reset_mock()
        broker.place_order("VSNT", 0.4, "SELL")
        mock_ib.placeOrder.assert_not_called()

def test_ib_cancel_open_orders(mock_ib):
    broker = InteractiveBrokersBroker(client_id=17)
    
    # Mock some trades
    trade1 = MagicMock()
    trade1.isDone.return_value = False
    trade1.order.clientId = 17
    
    trade2 = MagicMock()
    trade2.isDone.return_value = False
    trade2.order.clientId = 17
    
    trade_other = MagicMock()
    trade_other.isDone.return_value = False
    trade_other.order.clientId = 99 # Different client
    
    mock_ib.openTrades.return_value = [trade1, trade2, trade_other]
    
    cancelled = broker.cancel_open_orders()
    
    assert cancelled == 2
    assert mock_ib.cancelOrder.call_count == 2
    # Ensure it didn't cancel trade_other
    mock_ib.cancelOrder.assert_any_call(trade1.order)
    mock_ib.cancelOrder.assert_any_call(trade2.order)

def test_ib_contract_generation(mock_ib):
    from ib_async import Stock, Forex, Crypto, Future
    broker = InteractiveBrokersBroker()
    
    # Stock
    c1 = broker._build_contract({"sec_type": "STK", "symbol": "AAPL"})
    assert isinstance(c1, Stock)
    assert c1.symbol == "AAPL"
    
    # Forex
    c2 = broker._build_contract({"sec_type": "CASH", "symbol": "EURUSD"})
    assert isinstance(c2, Forex)
    assert c2.symbol == "EUR"
    assert c2.currency == "USD"
    
    # Crypto
    c3 = broker._build_contract({"sec_type": "CRYPTO", "symbol": "BTCUSD"})
    assert isinstance(c3, Crypto)
    assert c3.symbol == "BTCUSD"
    
    # Future
    c4 = broker._build_contract({"sec_type": "FUT", "symbol": "ES", "exchange": "CME"})
    assert isinstance(c4, Future)
    assert c4.symbol == "ES"
    assert c4.exchange == "CME"

def test_ib_map_symbol():
    broker = InteractiveBrokersBroker()
    # Mock SymbolMapper.map_symbol
    with patch.object(broker.symbol_mapper, 'map_symbol', return_value={
        "symbol": "EURUSD",
        "type": "forex",
        "source": "default-rule"
    }):
        meta = broker.map_symbol("EURUSD=X")
        assert meta["sec_type"] == "CASH"
        assert meta["exchange"] == "IDEALPRO"
        assert meta["broker_symbol"] == "EURUSD"

    with patch.object(broker.symbol_mapper, 'map_symbol', return_value={
        "symbol": "BTCUSD",
        "type": "crypto",
        "source": "default-rule"
    }):
        meta = broker.map_symbol("BTC-USD")
        assert meta["sec_type"] == "CRYPTO"
        assert meta["exchange"] == "PAXOS"
