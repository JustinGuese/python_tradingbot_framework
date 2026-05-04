import pytest
import json
from unittest.mock import MagicMock, patch
from tradingbot.livetrade.etoro import EtoroBroker

@pytest.fixture
def broker():
    return EtoroBroker(api_key="test_api", user_key="test_user", demo=True)

def test_etoro_get_cash(broker):
    with patch.object(broker.client, 'get') as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "portfolio": {
                    "cash": 1234.56,
                    "equity": 5000.0,
                    "positions": []
                }
            }
        )
        assert broker.get_cash() == 1234.56
        mock_get.assert_called_once()
        # Check headers
        headers = mock_get.call_args.kwargs.get('headers')
        assert 'x-request-id' in headers

def test_etoro_get_total_equity(broker):
    with patch.object(broker.client, 'get') as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "portfolio": {
                    "cash": 1000.0,
                    "equity": 5432.10,
                    "positions": []
                }
            }
        )
        assert broker.get_total_equity() == 5432.10

def test_etoro_get_positions(broker):
    with patch.object(broker.client, 'get') as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "portfolio": {
                    "positions": [
                        {"instrumentId": 1, "units": 10.0, "positionId": "pos1"},
                        {"instrumentId": 2, "units": 5.5, "positionId": "pos2"},
                        {"instrumentId": 1, "units": 2.0, "positionId": "pos3"}
                    ]
                }
            }
        )
        positions = broker.get_positions()
        assert positions == {"1": 12.0, "2": 5.5}
        assert broker._position_id_map == {"1": "pos3", "2": "pos2"}

def test_etoro_place_order_buy(broker):
    with patch.object(broker.client, 'get') as mock_get, \
         patch.object(broker.client, 'post') as mock_post:
        
        # Mock price fetch
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"instruments": [{"instrumentId": 1, "lastPrice": 150.0}]}
        )
        
        # Mock BUY order
        mock_post.return_value = MagicMock(status_code=201, json=lambda: {"token": "t1"})
        
        # 10 shares @ 150 = 1500 USD
        broker.place_order("1", 10.0, "BUY")
        
        mock_post.assert_called_once()
        payload = mock_post.call_args.kwargs.get('json')
        assert payload["InstrumentID"] == 1
        assert payload["IsBuy"] is True
        assert payload["Amount"] == 1500.0

def test_etoro_place_order_sell(broker):
    # Setup position map
    broker._position_id_map = {"1": "pos_123"}
    
    with patch.object(broker.client, 'post') as mock_post:
        mock_post.return_value = MagicMock(status_code=201, json=lambda: {"token": "t2"})
        
        broker.place_order("1", 5.0, "SELL")
        
        mock_post.assert_called_once()
        url = mock_post.call_args.args[0]
        assert "pos_123" in url

def test_etoro_map_symbol(broker):
    with patch.object(broker.client, 'get') as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"results": [{"instrumentId": 1001, "instrumentDisplayName": "Apple"}]}
        )
        
        meta = broker.map_symbol("AAPL")
        assert meta["symbol"] == "1001"
        assert meta["source"] == "etoro_search"
        
        # Test caching (second call shouldn't hit API)
        mock_get.reset_mock()
        meta2 = broker.map_symbol("AAPL")
        assert meta2["symbol"] == "1001"
        mock_get.assert_not_called()

def test_etoro_search_symbol(broker):
    with patch.object(broker.client, 'get') as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "results": [
                    {"instrumentId": 1, "instrumentDisplayName": "Apple", "exchangeName": "NASDAQ"},
                    {"instrumentId": 2, "instrumentDisplayName": "Tesla", "exchangeName": "NASDAQ"}
                ]
            }
        )
        results = broker.search_symbol("test")
        assert len(results) == 2
        assert results[0]["symbol"] == "1"
        assert results[0]["description"] == "Apple"
