import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
from tradingbot.livetrade.darwinex import DarwinexBroker

@pytest.fixture
def broker():
    return DarwinexBroker(username="test_user", password="test_password", account_id="acc_123", demo=True)

def test_darwinex_login(broker):
    with patch.object(broker.client, 'post') as mock_post:
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"sessionToken": "test_token"}
        )
        broker._login()
        assert broker._session_token == "test_token"
        assert broker._token_expires_at > datetime.now()
        mock_post.assert_called_once()

def test_darwinex_get_cash(broker):
    broker._session_token = "valid_token"
    broker._token_expires_at = datetime.now() + timedelta(hours=1)
    
    with patch.object(broker.client, 'get') as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"balance": 10000.50, "equity": 12000.75}
        )
        assert broker.get_cash() == 10000.50
        mock_get.assert_called_once_with(
            "/accounts/acc_123/metrics",
            params=None,
            headers={"Authorization": "DXAPI valid_token", "Content-Type": "application/json"}
        )

def test_darwinex_get_total_equity(broker):
    broker._session_token = "valid_token"
    broker._token_expires_at = datetime.now() + timedelta(hours=1)
    
    with patch.object(broker.client, 'get') as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"balance": 10000.50, "equity": 12000.75}
        )
        assert broker.get_total_equity() == 12000.75

def test_darwinex_get_positions(broker):
    broker._session_token = "valid_token"
    broker._token_expires_at = datetime.now() + timedelta(hours=1)
    
    with patch.object(broker.client, 'get') as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "positions": [
                    {"instrumentCode": "EURUSD", "quantity": 1000, "side": "BUY", "openPrice": 1.10},
                    {"instrumentCode": "GBPUSD", "quantity": 500, "side": "SELL", "openPrice": 1.30},
                    {"instrumentCode": "EURUSD", "quantity": 200, "side": "BUY", "openPrice": 1.11}
                ]
            }
        )
        positions = broker.get_positions()
        assert positions == {"EURUSD": 1200, "GBPUSD": -500}

def test_darwinex_place_order(broker):
    broker._session_token = "valid_token"
    broker._token_expires_at = datetime.now() + timedelta(hours=1)
    
    with patch.object(broker.client, 'post') as mock_post:
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"orderId": "ord_1"})
        
        broker.place_order("AAPL.US", 10.0, "BUY")
        
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "/accounts/acc_123/orders"
        assert kwargs["json"] == {
            "instrument": "AAPL.US",
            "quantity": 10.0,
            "side": "BUY",
            "type": "MARKET"
        }

def test_darwinex_map_symbol(broker):
    broker._session_token = "valid_token"
    broker._token_expires_at = datetime.now() + timedelta(hours=1)
    
    with patch.object(broker.client, 'get') as mock_get:
        # Mock search results
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [
                {"symbol": "AAPL.US", "description": "Apple Inc", "type": "stock"}
            ]
        )
        
        # Test search fallback
        meta = broker.map_symbol("AAPL")
        assert meta["symbol"] == "AAPL.US"
        assert meta["source"] == "darwinex_search"
        
        # Test caching
        mock_get.reset_mock()
        meta2 = broker.map_symbol("AAPL")
        assert meta2["symbol"] == "AAPL.US"
        mock_get.assert_not_called()

def test_darwinex_cancel_orders(broker):
    broker._session_token = "valid_token"
    broker._token_expires_at = datetime.now() + timedelta(hours=1)
    
    with patch.object(broker.client, 'get') as mock_get, \
         patch.object(broker.client, 'delete') as mock_delete:
        
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [
                {"id": "o1", "status": "PENDING"},
                {"id": "o2", "status": "PENDING"}
            ]
        )
        mock_delete.return_value = MagicMock(status_code=200, json=lambda: {})
        
        count = broker.cancel_open_orders()
        assert count == 2
        assert mock_delete.call_count == 2
