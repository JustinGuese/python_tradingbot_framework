from unittest.mock import MagicMock, patch

import pytest

from tradingbot.livetrade.collective2 import Collective2Broker


@pytest.fixture
def broker():
    return Collective2Broker(api_key="test_api", system_id="155809898")


def _response(payload):
    return MagicMock(status_code=200, json=lambda: payload, raise_for_status=lambda: None)


def _position(full_symbol, quantity, exchange_symbol=None):
    """One row of GetStrategyOpenPositions, in the shape C2 API v4 actually returns."""
    return {
        "StrategyName": "AdaptiveMeanReversionBot",
        "StrategyId": 155809898,
        "Currency": "USD",
        "Quantity": quantity,
        "AvgPx": 100.0,
        "C2Symbol": {"FullSymbol": full_symbol, "SymbolType": "stock"},
        "ExchangeSymbol": {"Symbol": exchange_symbol or full_symbol, "Currency": "USD"},
    }


def test_c2_get_positions_reads_the_nested_symbol(broker):
    """
    The ticker lives in C2Symbol.FullSymbol; there is no top-level "Symbol" key.

    Reading pos["Symbol"] returned None for every row, so a book of 113 QQQ and
    152 TQQQ collapsed to {None: 152}. sync() reconciles to target state, so
    invisible holdings read as "not held" and the copier re-bought the whole
    target on top of the existing book.
    """
    with patch.object(broker.client, "get") as mock_get:
        mock_get.return_value = _response({"Results": [_position("QQQ", 113), _position("TQQQ", 152)]})
        assert broker.get_positions() == {"QQQ": 113.0, "TQQQ": 152.0}


def test_c2_get_positions_falls_back_to_exchange_symbol(broker):
    with patch.object(broker.client, "get") as mock_get:
        row = _position("QQQ", 10)
        row["C2Symbol"] = {}  # no FullSymbol -> ExchangeSymbol.Symbol must carry it
        mock_get.return_value = _response({"Results": [row]})
        assert broker.get_positions() == {"QQQ": 10.0}


def test_c2_get_positions_sums_lots_for_one_ticker(broker):
    """Assignment would keep only the last lot and under-report the holding."""
    with patch.object(broker.client, "get") as mock_get:
        mock_get.return_value = _response({"Results": [_position("QQQ", 100), _position("QQQ", 13)]})
        assert broker.get_positions() == {"QQQ": 113.0}


def test_c2_get_positions_preserves_short_sign(broker):
    with patch.object(broker.client, "get") as mock_get:
        mock_get.return_value = _response({"Results": [_position("QQQ", -25)]})
        assert broker.get_positions() == {"QQQ": -25.0}


def test_c2_get_positions_raises_on_unresolvable_symbol(broker):
    """
    An unreadable position must abort the sync, not silently shrink the book.

    Skipping the row would under-report holdings and the copier would re-buy
    them; sync() catches the raise and aborts, which is the safe outcome.
    """
    with patch.object(broker.client, "get") as mock_get:
        row = _position("QQQ", 10)
        row["C2Symbol"] = {}
        row["ExchangeSymbol"] = {}
        mock_get.return_value = _response({"Results": [row]})
        with pytest.raises(ValueError, match="no resolvable symbol"):
            broker.get_positions()


def test_c2_get_positions_propagates_transport_failure(broker):
    """A failed positions call must not look like a flat account."""
    with patch.object(broker.client, "get") as mock_get:
        mock_get.side_effect = RuntimeError("C2 503")
        with pytest.raises(RuntimeError):
            broker.get_positions()


def test_c2_get_positions_empty_book(broker):
    with patch.object(broker.client, "get") as mock_get:
        mock_get.return_value = _response({"Results": []})
        assert broker.get_positions() == {}


def _supported(underlying, symbol_type, description="", exchange="DEFAULT"):
    """One GetSupportedSymbols row. Note C2Symbol has Underlying, NOT FullSymbol."""
    return {
        "Description": description,
        "C2Symbol": {"SymbolType": symbol_type, "Underlying": underlying, "Description": description},
        "ExchangeSymbol": {"Symbol": underlying, "SecurityExchange": exchange},
    }


def test_c2_search_symbol_reads_nested_fields_and_filters(broker):
    """
    GetSupportedSymbols ignores SearchText and returns the whole universe, with
    no top-level Symbol/SymbolType/Exchange. Reading the flat keys yielded 188
    rows of symbol=None all typed "stock", futures and forex included.
    """
    universe = {
        "Results": [
            _supported("@YM", "future", "MINI DOW [CBOT]", "XCBT"),
            _supported("EUR/USD", "forex"),
            _supported("QQQ", "stock", "Invesco QQQ Trust"),
            _supported("BRK.B", "stock", "Berkshire class B"),
        ]
    }
    with patch.object(broker.client, "get") as mock_get:
        mock_get.return_value = _response(universe)
        assert broker.search_symbol("QQQ") == [
            {
                "symbol": "QQQ",
                "description": "Invesco QQQ Trust",
                "type": "stock",
                "exchange": "DEFAULT",
                "score": 100,
            }
        ]
        assert broker.search_symbol("@YM")[0]["type"] == "future"
        assert broker.search_symbol("EUR/USD")[0]["symbol"] == "EUR/USD"


def test_c2_search_symbol_ranks_exact_ticker_above_description_match(broker):
    universe = {"Results": [_supported("PSQ", "stock", "Short QQQ ETF"), _supported("QQQ", "stock", "Invesco QQQ")]}
    with patch.object(broker.client, "get") as mock_get:
        mock_get.return_value = _response(universe)
        assert [c["symbol"] for c in broker.search_symbol("QQQ")] == ["QQQ", "PSQ"]


def test_c2_position_rows_render_nested_symbol_and_avgpx(broker):
    """The summary table read Symbol/SymbolType/OpenPrice — none of which exist."""
    row = _position("QQQ", 113)
    row["AvgPx"] = 656.35071
    with patch.object(broker.client, "get") as mock_get:
        mock_get.return_value = _response({"Results": [row]})
        header, rows = broker._position_rows()
    assert "AvgPrice" in header
    assert "QQQ" in rows[0]
    assert "stock" in rows[0]
    assert "656.3507" in rows[0]


def test_c2_cancel_open_orders_cancels_every_working_order(broker):
    """
    GetStrategyOpenPositions does not reflect unfilled orders, so a second sync
    before the first filled recomputed the same deltas and double-submitted them:
    147 QQQ sells against 113 shares held. cancel_open_orders() is what stops that.
    """
    active = {
        "Results": [
            {"SignalId": 157376131, "OrderQuantity": 73, "C2Symbol": {"FullSymbol": "QQQ"}},
            {"SignalId": 157376133, "OrderQuantity": 34, "C2Symbol": {"FullSymbol": "TQQQ"}},
        ]
    }
    with patch.object(broker.client, "get") as mock_get, patch.object(broker.client, "request") as mock_req:
        mock_get.return_value = _response(active)
        mock_req.return_value = _response({"Results": [{"SignalId": 157376131}]})
        assert broker.cancel_open_orders() == 2
    # Both ids are mandatory: SignalId alone 400s with "StrategyId: Missing value".
    for call, sig in zip(mock_req.call_args_list, [157376131, 157376133], strict=True):
        assert call.args[0] == "DELETE"
        assert call.kwargs["params"] == {"SignalId": sig, "StrategyId": 155809898}


def test_c2_cancel_open_orders_no_working_orders(broker):
    with patch.object(broker.client, "get") as mock_get, patch.object(broker.client, "request") as mock_req:
        mock_get.return_value = _response({"Results": []})
        assert broker.cancel_open_orders() == 0
        mock_req.assert_not_called()


def test_c2_place_order_logs_accepted_order_as_success(broker, caplog):
    """
    An accepted v4 order returns Results[].SignalId — there is no "Success" key.

    Checking result["Success"] was falsy for every accepted order, so real
    submissions logged "C2 Order Failed: Unknown error" and looked broken.
    """
    accepted = {"Results": [{"SignalId": 157376139}], "ResponseStatus": {"ErrorCode": "200"}}
    with patch.object(broker.client, "post") as mock_post:
        mock_post.return_value = _response(accepted)
        with caplog.at_level("INFO"):
            broker.place_order("QQQ", 73, "SELL", symbol_type="stock")
    assert "C2 Order Success: SignalId 157376139" in caplog.text
    assert "C2 Order Failed" not in caplog.text


def test_c2_place_order_reports_field_level_rejection(broker, caplog):
    """A rejection must surface C2's own reason, not a generic 'Unknown error'."""
    rejected = {
        "ResponseStatus": {
            "ErrorCode": "400",
            "Message": "BadRequest",
            "Errors": [{"ErrorCode": "1121", "FieldName": "Symbol", "Message": "Invalid symbol"}],
        }
    }
    with patch.object(broker.client, "post") as mock_post:
        mock_post.return_value = _response(rejected)
        with caplog.at_level("INFO"):
            broker.place_order("ZZZZNOTREAL", 1, "SELL", symbol_type="stock")
    assert "C2 Order Failed: Symbol: Invalid symbol" in caplog.text
    assert "C2 Order Success" not in caplog.text


@pytest.mark.parametrize("symbol", ["QQQ", "TQQQ", "TMF", "DBO", "^XAU", "EURUSD=X"])  # ^XAU maps to GDX
def test_c2_is_tradeable_accepts_us_listings(broker, symbol):
    assert broker.is_tradeable(symbol) is True


@pytest.mark.parametrize("symbol", ["RENW.DE", "IWDA.AS", "BTEC.L", "BTC-USD", "AVAX-USD", "^DJI"])
def test_c2_is_tradeable_rejects_venues_c2_cannot_fill(broker, symbol):
    """The default mapper passes these through unchanged, so nothing else stops
    a nightly order C2 rejects."""
    assert broker.is_tradeable(symbol) is False
