import itertools
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tradingbot.livetrade.interactive_brokers import InteractiveBrokersBroker

STK_META = {"symbol": "AAPL", "type": "stock", "sec_type": "STK", "exchange": "SMART", "currency": "USD"}


def _val(tag, value, currency="USD", account="DU123"):
    return SimpleNamespace(tag=tag, value=str(value), currency=currency, account=account)


def _pos(symbol, qty, avg=10.0):
    return SimpleNamespace(contract=SimpleNamespace(symbol=symbol), position=qty, avgCost=avg)


@pytest.fixture
def broker():
    """A broker whose ib_async IB() is a mock that 'connects' instantly."""
    with patch("tradingbot.livetrade.interactive_brokers.IB") as cls:
        ib = cls.return_value
        ib.managedAccounts.return_value = ["DU123"]
        ib.accountValues.return_value = [_val("TotalCashValue", 100000.0), _val("NetLiquidation", 150000.0)]
        ib.positions.return_value = [_pos("AAPL", 10.0)]
        ib.openTrades.return_value = []
        b = InteractiveBrokersBroker(account_id="DU123", client_id=17)
        yield b


def test_ib_connect_uses_host_port_client_id(broker):
    broker.host, broker.port = "gw", 4002
    broker.connect()
    args, kwargs = broker.ib.connect.call_args
    assert args == ("gw", 4002)
    assert kwargs["clientId"] == 17


def test_ib_connect_rejects_unmanaged_account(broker):
    broker.account_id = "DU999"
    with pytest.raises(ValueError, match="not managed"):
        broker.connect()
    assert broker._connected is False


def test_ib_is_sandbox_for_du_accounts():
    assert InteractiveBrokersBroker(account_id="DU123").is_sandbox
    assert not InteractiveBrokersBroker(account_id="U123").is_sandbox


def test_ib_get_cash_and_equity(broker):
    assert broker.get_cash() == 100000.0
    assert broker.get_total_equity() == 150000.0


def test_ib_get_cash_ignores_other_accounts(broker):
    broker.ib.accountValues.return_value = [
        _val("TotalCashValue", 1.0, account="DU999"),
        _val("TotalCashValue", 42.0, account="DU123"),
    ]
    assert broker.get_cash() == 42.0


def test_ib_get_total_equity_falls_back_to_portfolio_plus_cash(broker):
    broker.ib.accountValues.return_value = [_val("TotalCashValue", 500.0)]
    broker.ib.accountSummary.return_value = []
    broker.ib.sleep = lambda _s: None
    broker.ib.portfolio.return_value = [SimpleNamespace(marketValue=2000.0)]
    # Skip the 5s wait for account values (logging also reads time.time, so no finite side_effect list).
    clock = itertools.count(0, 10)
    with patch("tradingbot.livetrade.interactive_brokers.time.time", side_effect=lambda: next(clock)):
        assert broker.get_total_equity() == 2500.0


def test_ib_get_positions_skips_zero_and_sums_duplicates(broker):
    broker.ib.positions.return_value = [_pos("AAPL", 5.0), _pos("AAPL", 2.0), _pos("FLAT", 0.0)]
    assert broker.get_positions() == {"AAPL": 7.0}


def _place(broker, qty, side="BUY", contract=None):
    contract = contract or MagicMock(name="contract")
    trade = MagicMock()
    trade.orderStatus.status = "Submitted"
    broker.ib.placeOrder.return_value = trade
    with (
        patch.object(broker.symbol_mapper, "unmap_symbol", return_value="AAPL"),
        patch.object(broker, "map_symbol", return_value=STK_META),
        patch.object(broker, "_resolve_stk_contract", return_value=contract),
    ):
        broker.place_order("AAPL", qty, side)
    return trade


def test_ib_place_order_floors_to_whole_shares(broker):
    _place(broker, 10.5)
    order = broker.ib.placeOrder.call_args[0][1]
    assert order.action == "BUY"
    assert order.totalQuantity == 10.0
    assert order.orderType == "MKT"
    assert order.account == "DU123"


def test_ib_place_order_skips_sub_one_quantity(broker):
    _place(broker, 0.4, side="SELL")
    broker.ib.placeOrder.assert_not_called()


def test_ib_place_order_sell_reuses_held_contract(broker):
    held = _pos("AAPL", 10.0)
    held.contract = SimpleNamespace(symbol="AAPL")
    broker.ib.positions.return_value = [held]
    _place(broker, 3.0, side="SELL")
    assert broker.ib.placeOrder.call_args[0][0] is held.contract


def test_ib_place_order_raises_when_gateway_rejects(broker):
    trade = MagicMock()
    trade.orderStatus.status = "Cancelled"
    trade.log = [SimpleNamespace(message="insufficient buying power")]
    broker.ib.placeOrder.return_value = trade
    with (
        patch.object(broker.symbol_mapper, "unmap_symbol", return_value="AAPL"),
        patch.object(broker, "map_symbol", return_value=STK_META),
        patch.object(broker, "_resolve_stk_contract", return_value=MagicMock()),
        pytest.raises(RuntimeError, match="insufficient buying power"),
    ):
        broker.place_order("AAPL", 5.0, "BUY")


def test_ib_cancel_open_orders_only_own_client_id(broker):
    def _trade(client_id, done=False):
        t = MagicMock()
        t.order.clientId = client_id
        t.isDone.return_value = done
        return t

    mine, done, other = _trade(17), _trade(17, done=True), _trade(99)
    broker.ib.openTrades.return_value = [mine, done, other]

    assert broker.cancel_open_orders() == 1
    broker.ib.cancelOrder.assert_called_once_with(mine.order)


def test_ib_map_symbol():
    broker = InteractiveBrokersBroker()
    with patch.object(
        broker.symbol_mapper,
        "map_symbol",
        return_value={"symbol": "EURUSD", "type": "forex", "source": "default-rule"},
    ):
        meta = broker.map_symbol("EURUSD=X")
        assert meta is not None
        assert meta["symbol"] == "EURUSD"
        assert meta["sec_type"] == "CASH"
        assert meta["exchange"] == "IDEALPRO"


def test_ib_map_symbol_defaults_to_us_equity():
    broker = InteractiveBrokersBroker()
    with patch.object(
        broker.symbol_mapper,
        "map_symbol",
        return_value={"symbol": "QQQ", "type": "stock", "source": "default-rule"},
    ):
        meta = broker.map_symbol("QQQ")
        assert meta is not None
        assert meta["sec_type"] == "STK"
        assert meta["exchange"] == "SMART"
        assert meta["currency"] == "USD"


# --- UCITS mapping / eligibility ------------------------------------------------


def _real_broker():
    """A broker on the shipped symbol_map.json with a mocked IB session."""
    with patch("tradingbot.livetrade.interactive_brokers.IB") as cls:
        ib = cls.return_value
        ib.managedAccounts.return_value = ["DU123"]
        b = InteractiveBrokersBroker(account_id="DU123")
        b.ib = ib
        return b


def test_ib_map_symbol_returns_ucits_line_pinned_by_conid():
    meta = _real_broker().map_symbol("SPY")
    assert meta is not None
    assert (meta["symbol"], meta["conid"], meta["exchange"], meta["currency"]) == ("CSPX", 76023663, "LSEETF", "USD")
    assert meta["price_symbol"] == "CSPX.L"


def test_ib_build_contract_uses_conid():
    broker = _real_broker()
    contract = broker._build_contract(broker.map_symbol("TLT"))
    assert contract.conId == 181150836
    assert contract.exchange == "LSEETF"


def test_ib_unmappable_etf_is_not_tradeable_and_not_mapped():
    broker = _real_broker()
    assert broker.is_tradeable("UUP") is False  # explicit tradeable:false
    assert broker.map_symbol("UUP") is None
    assert broker.is_tradeable("SPY") is True  # explicit UCITS entry, no IB lookup
    broker.ib.reqContractDetails.assert_not_called()


@pytest.mark.parametrize(("stock_type", "expected"), [("ETF", False), ("ETC", False), ("COMMON", True), ("ADR", True)])
def test_ib_unmapped_symbol_eligibility_follows_ib_stock_type(stock_type, expected):
    broker = _real_broker()
    broker.ib.reqContractDetails.return_value = [SimpleNamespace(stockType=stock_type)]
    assert broker.is_tradeable("ZZZZ") is expected
    assert broker.is_tradeable("ZZZZ") is expected  # cached
    assert broker.ib.reqContractDetails.call_count == 1


def test_ib_unmapped_symbol_unknown_to_ib_is_not_tradeable():
    broker = _real_broker()
    broker.ib.reqContractDetails.return_value = []
    assert broker.is_tradeable("ZZZZ") is False


def test_ib_exchange_suffix_symbol_without_override_is_not_tradeable():
    assert _real_broker().is_tradeable("IWDA.AS") is False


def test_ib_price_uses_the_mapped_lines_own_yfinance_ticker():
    broker = _real_broker()
    with (
        patch.object(broker, "_get_native_price", return_value=0.0),
        patch.object(broker, "_yf_last_price", return_value=3.08) as yfp,
    ):
        assert broker.get_latest_price("IDTL") == 3.08
    yfp.assert_called_once_with("IDTL.L")  # not TLT


def test_ib_yf_price_refuses_non_usd_quote_and_caches():
    broker = _real_broker()
    with patch("tradingbot.livetrade.interactive_brokers.yf.Ticker") as tk:
        tk.return_value.fast_info = {"currency": "GBp", "lastPrice": 800.0}
        tk.return_value.history.return_value = MagicMock(empty=True)
        assert broker._yf_last_price("X.L") == 0.0
        tk.return_value.fast_info = {"currency": "USD", "lastPrice": 3.08}
        assert broker._yf_last_price("IDTL.L") == 3.08
        assert broker._yf_last_price("IDTL.L") == 3.08
        assert tk.call_count == 2  # X.L once, IDTL.L once (second call cached)


def test_ib_place_order_buys_mapped_line_by_conid():
    broker = _real_broker()
    trade = MagicMock()
    trade.orderStatus.status = "Submitted"
    broker.ib.positions.return_value = []
    broker.ib.placeOrder.return_value = trade
    broker.place_order("CSPX", 3.7, "BUY")
    contract, order = broker.ib.placeOrder.call_args[0]
    assert contract.conId == 76023663
    assert order.totalQuantity == 3.0


def test_symbol_map_ib_entries_are_complete():
    """Every IB override is either explicitly untradeable or fully pinned, with a
    price ticker for sizing; a half-filled entry would size or route wrongly."""
    broker = _real_broker()
    seen: dict[str, str] = {}
    for yf, entry in broker.symbol_mapper.overrides.items():
        slot = entry.get("interactive_brokers")
        if slot is None:
            continue
        if slot.get("tradeable") is False:
            continue
        assert slot.get("conid") and slot.get("exchange") and slot.get("currency") == "USD", yf
        assert slot.get("price_symbol"), f"{yf}: price_symbol missing, sizing would use the US ticker"
        seen.setdefault(slot["symbol"], slot["conid"])
        assert seen[slot["symbol"]] == slot["conid"], f"{yf}: same ticker, different conid"
