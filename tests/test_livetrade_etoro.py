from unittest.mock import MagicMock, patch

import pytest

from tradingbot.livetrade.etoro import EtoroBroker


@pytest.fixture
def broker():
    return EtoroBroker(api_key="test_api", user_key="test_user", demo=True)


def _response(payload):
    return MagicMock(status_code=200, json=lambda: payload)


def _routed_get(portfolio=None, rates=None):
    """
    Dispatch client.get by URL.

    get_total_equity() marks positions to market, so it calls the portfolio
    endpoint AND the rates endpoint. A single return_value would feed the
    portfolio payload to the price lookup and silently value the book at 0.
    """

    def _get(url, *args, **kwargs):
        if "rates" in url:
            return _response(rates or {"rates": []})
        return _response(portfolio or {})

    return _get


def test_etoro_get_cash(broker):
    with patch.object(broker.client, "get") as mock_get:
        # eToro returns cash as 'credit' inside a 'clientPortfolio' envelope.
        mock_get.return_value = _response({"clientPortfolio": {"credit": 1234.56, "positions": []}})
        assert broker.get_cash() == 1234.56
        mock_get.assert_called_once()
        # Check headers
        headers = mock_get.call_args.kwargs.get("headers")
        assert headers is not None
        assert "x-request-id" in headers


def test_etoro_get_total_equity(broker):
    """eToro reports no equity field, so the adapter computes credit + mark-to-market."""
    with patch.object(broker.client, "get") as mock_get:
        mock_get.side_effect = _routed_get(
            portfolio={
                "clientPortfolio": {
                    "credit": 1000.0,
                    "positions": [{"instrumentID": 1, "units": 10.0, "openRate": 400.0}],
                }
            },
            rates={"rates": [{"lastExecution": 443.21}]},
        )
        # 1000 credit + 10 units * 443.21 last price = 5432.10
        assert broker.get_total_equity() == pytest.approx(5432.10)


def test_etoro_total_equity_falls_back_to_open_rate_without_a_price(broker):
    with patch.object(broker.client, "get") as mock_get:
        mock_get.side_effect = _routed_get(
            portfolio={
                "clientPortfolio": {
                    "credit": 1000.0,
                    "positions": [{"instrumentID": 1, "units": 10.0, "openRate": 400.0}],
                }
            },
            rates={"rates": []},
        )
        # No price available -> values the leg at units * openRate.
        assert broker.get_total_equity() == pytest.approx(5000.0)


def test_etoro_get_positions(broker):
    with patch.object(broker.client, "get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "portfolio": {
                    "positions": [
                        {"instrumentId": 1, "units": 10.0, "positionId": "pos1"},
                        {"instrumentId": 2, "units": 5.5, "positionId": "pos2"},
                        {"instrumentId": 1, "units": 2.0, "positionId": "pos3"},
                    ]
                }
            },
        )
        positions = broker.get_positions()
        assert positions == {"1": 12.0, "2": 5.5}
        # Every lot is retained, not just the last one seen per instrument —
        # a sized SELL needs them all.
        assert broker._position_lots == {"1": [("pos1", 10.0), ("pos3", 2.0)], "2": [("pos2", 5.5)]}


def test_etoro_get_positions_propagates_transport_failure(broker):
    """
    A failed positions call must not look like a flat account.

    sync() does full target-state reconciliation, so swallowing this into {}
    made the copier re-buy the entire book on top of positions it already held.
    """
    with patch.object(broker.client, "get") as mock_get:
        mock_get.side_effect = RuntimeError("eToro 503")
        with pytest.raises(RuntimeError):
            broker.get_positions()


def test_etoro_place_order_buy(broker):
    with patch.object(broker.client, "get") as mock_get, patch.object(broker.client, "post") as mock_post:
        # Price comes from the market-data rates endpoint, keyed 'lastExecution'.
        # If this shape is wrong the adapter reads price 0 and returns without
        # posting anything, so the assertions below are what catch that.
        mock_get.return_value = _response({"rates": [{"lastExecution": 150.0}]})

        # Mock BUY order
        mock_post.return_value = MagicMock(status_code=201, json=lambda: {"token": "t1"})

        # 10 shares @ 150 = 1500 USD
        broker.place_order("1", 10.0, "BUY")

        mock_post.assert_called_once()
        payload = mock_post.call_args.kwargs.get("json")
        assert payload is not None
        assert payload["InstrumentID"] == 1
        assert payload["IsBuy"] is True
        assert payload["Amount"] == 1500.0


def test_etoro_place_order_sell(broker):
    # Single lot, sold in full.
    broker._position_lots = {"1": [("pos_123", 5.0)]}

    with patch.object(broker.client, "post") as mock_post:
        mock_post.return_value = MagicMock(status_code=201, json=lambda: {"token": "t2"})

        broker.place_order("1", 5.0, "SELL")

        mock_post.assert_called_once()
        url = mock_post.call_args.args[0]
        assert "pos_123" in url


def _closed_position_ids(mock_post):
    """positionIds appearing in the close URLs the adapter posted to."""
    return [call.args[0].rsplit("/", 1)[-1] for call in mock_post.call_args_list]


def test_etoro_partial_sell_does_not_liquidate_the_whole_position(broker):
    """
    The regression this fix exists for.

    eToro has no partial-close endpoint, so the adapter used to close the single
    last-seen lot for ANY sell size — turning a 25% trim into a full exit that
    the next sync bought straight back.
    """
    broker._position_lots = {"1": [("lot_a", 6.0), ("lot_b", 3.0), ("lot_c", 1.0)]}

    with patch.object(broker.client, "post") as mock_post:
        mock_post.return_value = MagicMock(status_code=201, json=lambda: {"token": "t"})

        # Hold 10 units, sell 4 -> largest-fitting lots are 3 + 1.
        broker.place_order("1", 4.0, "SELL")

        assert _closed_position_ids(mock_post) == ["lot_b", "lot_c"]


def test_etoro_sell_never_overshoots_the_requested_quantity(broker):
    """Under-selling leaves the next reconciliation to trim again; overshooting
    sells inventory the strategy asked to keep."""
    broker._position_lots = {"1": [("lot_a", 6.0), ("lot_b", 3.0)]}

    with patch.object(broker.client, "post") as mock_post:
        mock_post.return_value = MagicMock(status_code=201, json=lambda: {"token": "t"})

        broker.place_order("1", 5.0, "SELL")

        # Only the 3-unit lot fits within 5; closing the 6-unit lot would oversell.
        assert _closed_position_ids(mock_post) == ["lot_b"]


def test_etoro_sell_smaller_than_smallest_lot_does_nothing(broker):
    broker._position_lots = {"1": [("lot_a", 6.0)]}

    with patch.object(broker.client, "post") as mock_post:
        broker.place_order("1", 1.0, "SELL")
        mock_post.assert_not_called()


def test_etoro_full_exit_closes_every_lot(broker):
    broker._position_lots = {"1": [("lot_a", 6.0), ("lot_b", 3.0), ("lot_c", 1.0)]}

    with patch.object(broker.client, "post") as mock_post:
        mock_post.return_value = MagicMock(status_code=201, json=lambda: {"token": "t"})

        broker.place_order("1", 10.0, "SELL")

        assert sorted(_closed_position_ids(mock_post)) == ["lot_a", "lot_b", "lot_c"]


def test_etoro_map_symbol(broker):
    with patch.object(broker.client, "get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200, json=lambda: {"results": [{"instrumentId": 1001, "instrumentDisplayName": "Apple"}]}
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
    with patch.object(broker.client, "get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "results": [
                    {"instrumentId": 1, "instrumentDisplayName": "Apple", "exchangeName": "NASDAQ"},
                    {"instrumentId": 2, "instrumentDisplayName": "Tesla", "exchangeName": "NASDAQ"},
                ]
            },
        )
        results = broker.search_symbol("test")
        assert len(results) == 2
        assert results[0]["symbol"] == "1"
        assert results[0]["description"] == "Apple"


def test_etoro_cancel_open_orders_cancels_all_and_returns_count(broker):
    """Cancels both pending open and close orders via v2 execution endpoints and returns count."""
    portfolio_payload = {
        "clientPortfolio": {
            "orders": [{"orderID": "101"}],
            "ordersForClose": [{"orderID": "102"}],
        }
    }
    with patch.object(broker.client, "get") as mock_get, patch.object(broker.client, "delete") as mock_delete:
        mock_get.return_value = _response(portfolio_payload)
        mock_delete.return_value = MagicMock(status_code=200, text='{"token":"t"}', json=lambda: {"token": "t"})

        count = broker.cancel_open_orders()
        assert count == 2
        assert mock_delete.call_count == 2

        urls = [call.args[0] for call in mock_delete.call_args_list]
        assert f"{broker.BASE_URL}/api/v2/trading/execution/demo/orders/101" in urls
        assert f"{broker.BASE_URL}/api/v2/trading/execution/demo/orders/102" in urls

        # Check headers have x-request-id
        for call in mock_delete.call_args_list:
            headers = call.kwargs.get("headers")
            assert headers is not None
            assert "x-request-id" in headers

    # Also verify non-demo broker uses v2 without demo/ prefix
    live_broker = EtoroBroker(api_key="key", user_key="usr", demo=False)
    with patch.object(live_broker.client, "get") as mock_get, patch.object(live_broker.client, "delete") as mock_delete:
        mock_get.return_value = _response({"clientPortfolio": {"orders": [{"orderID": "201"}]}})
        mock_delete.return_value = MagicMock(status_code=200, text='{"token":"t"}', json=lambda: {"token": "t"})

        assert live_broker.cancel_open_orders() == 1
        urls = [call.args[0] for call in mock_delete.call_args_list]
        assert f"{live_broker.BASE_URL}/api/v2/trading/execution/orders/201" in urls


def test_etoro_cancel_open_orders_tolerates_already_cancelled(broker):
    """Cancelling an already-cancelled or already-filled order (e.g. 404) does not raise."""
    portfolio_payload = {
        "clientPortfolio": {
            "orders": [{"orderID": "101"}, {"orderID": "102"}],
        }
    }

    def _delete_dispatch(url, *args, **kwargs):
        if "102" in url:
            # 102 was already cancelled/filled -> 404
            return MagicMock(status_code=404, text='{"message":"Order already cancelled"}')
        return MagicMock(status_code=200, text='{"token":"ok"}', json=lambda: {"token": "ok"})

    with patch.object(broker.client, "get") as mock_get, patch.object(broker.client, "delete") as mock_delete:
        mock_get.return_value = _response(portfolio_payload)
        mock_delete.side_effect = _delete_dispatch

        # Must not raise; order 101 succeeded, order 102 was already cancelled
        count = broker.cancel_open_orders()
        assert count == 1
        assert mock_delete.call_count == 2


def test_etoro_cancel_open_orders_listing_failure_propagates(broker):
    """If listing pending orders fails, the copier must abort rather than trade on unknown state."""
    with patch.object(broker.client, "get") as mock_get, patch.object(broker.client, "delete") as mock_delete:
        mock_get.side_effect = RuntimeError("eToro 503 Service Unavailable")

        with pytest.raises(RuntimeError, match="eToro 503"):
            broker.cancel_open_orders()

        mock_delete.assert_not_called()


def test_etoro_cancel_open_orders_aborts_when_a_cancel_fails_for_real(broker):
    """Only 404/409/410 mean "already gone". A 5xx or a timeout leaves the order possibly
    working, so the sync must abort instead of submitting on top of it."""
    import httpx

    payload = {"clientPortfolio": {"orders": [{"orderID": "101"}]}}

    with patch.object(broker.client, "get") as mock_get, patch.object(broker.client, "delete") as mock_delete:
        mock_get.return_value = _response(payload)

        mock_delete.return_value = MagicMock(status_code=500, text="boom")
        with pytest.raises(httpx.HTTPStatusError):
            broker.cancel_open_orders()

        mock_delete.return_value = None
        mock_delete.side_effect = httpx.ReadTimeout("timed out")
        with pytest.raises(httpx.ReadTimeout):
            broker.cancel_open_orders()


def test_etoro_cancel_open_orders_scans_stock_orders(broker):
    """`stockOrders` is a real clientPortfolio key (seen in the live demo payload);
    after-hours ETF orders must be found there or the overnight double-submit survives."""
    payload = {"clientPortfolio": {"stockOrders": [{"orderID": "301"}]}}
    with patch.object(broker.client, "get") as mock_get, patch.object(broker.client, "delete") as mock_delete:
        mock_get.return_value = _response(payload)
        mock_delete.return_value = MagicMock(status_code=200, text="{}", json=lambda: {})
        assert broker.cancel_open_orders() == 1
        assert mock_delete.call_args.args[0].endswith("/api/v2/trading/execution/demo/orders/301")
