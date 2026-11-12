"""Tests for MT5 client protocol, FakeMT5, and connect_rpyc factory."""

from unittest.mock import MagicMock

from tests.mt5_fake import FakeMT5
from tradingbot.livetrade.mt5.client import (
    ACCOUNT_MARGIN_MODE_RETAIL_HEDGING,
    ACCOUNT_MARGIN_MODE_RETAIL_NETTING,
    ORDER_TYPE_BUY,
    ORDER_TYPE_SELL,
    TRADE_ACTION_DEAL,
    TRADE_RETCODE_DONE,
    TRADE_RETCODE_INVALID_VOLUME,
    TRADE_RETCODE_MARKET_CLOSED,
    MT5Client,
    connect_rpyc,
)


def test_fake_enforces_market_closed_rejection():
    """FakeMT5 must reject orders with retcode 10018 when market_closed is True."""
    fake = FakeMT5(market_closed=True)
    req = {
        "action": TRADE_ACTION_DEAL,
        "symbol": "SPY",
        "volume": 1.0,
        "type": ORDER_TYPE_BUY,
    }
    result = fake.order_send(req)
    assert result.retcode == 10018
    assert result.retcode == TRADE_RETCODE_MARKET_CLOSED
    assert fake.positions_get(symbol="SPY") == ()

    # Toggle market open and confirm it accepts orders
    fake.set_market_closed(False)
    result_open = fake.order_send(req)
    assert result_open.retcode == TRADE_RETCODE_DONE
    positions = fake.positions_get(symbol="SPY")
    assert positions is not None
    assert len(positions) == 1
    assert positions[0].volume == 1.0


def test_fake_nets_positions():
    """In netting mode, trades update a single position per symbol without creating extra tickets."""
    fake = FakeMT5(margin_mode=ACCOUNT_MARGIN_MODE_RETAIL_NETTING)

    # First buy: opens net long
    res1 = fake.order_send(
        {
            "action": TRADE_ACTION_DEAL,
            "symbol": "SPY",
            "volume": 1.0,
            "type": ORDER_TYPE_BUY,
        }
    )
    assert res1.retcode == TRADE_RETCODE_DONE
    pos = fake.positions_get(symbol="SPY")
    assert pos is not None
    assert len(pos) == 1
    assert pos[0].volume == 1.0
    first_ticket = pos[0].ticket

    # Second buy: increases existing net position
    res2 = fake.order_send(
        {
            "action": TRADE_ACTION_DEAL,
            "symbol": "SPY",
            "volume": 2.0,
            "type": ORDER_TYPE_BUY,
        }
    )
    assert res2.retcode == TRADE_RETCODE_DONE
    pos = fake.positions_get(symbol="SPY")
    assert pos is not None
    assert len(pos) == 1
    assert pos[0].ticket == first_ticket
    assert pos[0].volume == 3.0

    # Sell 1.0: reduces net position
    res3 = fake.order_send(
        {
            "action": TRADE_ACTION_DEAL,
            "symbol": "SPY",
            "volume": 1.0,
            "type": ORDER_TYPE_SELL,
        }
    )
    assert res3.retcode == TRADE_RETCODE_DONE
    pos = fake.positions_get(symbol="SPY")
    assert pos is not None
    assert len(pos) == 1
    assert pos[0].volume == 2.0

    # Sell 2.0: closes net position
    res4 = fake.order_send(
        {
            "action": TRADE_ACTION_DEAL,
            "symbol": "SPY",
            "volume": 2.0,
            "type": ORDER_TYPE_SELL,
        }
    )
    assert res4.retcode == TRADE_RETCODE_DONE
    assert fake.positions_get(symbol="SPY") == ()


def test_fake_hedges_positions_and_prevents_overshoot():
    """In hedging mode, buys create separate tickets; sells target position id and cannot overshoot."""
    fake = FakeMT5(margin_mode=ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)

    # Two separate buys create two tickets
    res1 = fake.order_send(
        {
            "action": TRADE_ACTION_DEAL,
            "symbol": "SPY",
            "volume": 1.0,
            "type": ORDER_TYPE_BUY,
        }
    )
    res2 = fake.order_send(
        {
            "action": TRADE_ACTION_DEAL,
            "symbol": "SPY",
            "volume": 2.0,
            "type": ORDER_TYPE_BUY,
        }
    )
    assert res1.retcode == TRADE_RETCODE_DONE
    assert res2.retcode == TRADE_RETCODE_DONE

    positions = fake.positions_get(symbol="SPY")
    assert positions is not None
    assert len(positions) == 2
    ticket1, ticket2 = positions[0].ticket, positions[1].ticket
    assert ticket1 != ticket2

    # Attempting to oversell ticket2 by volume > 2.0 is rejected
    res_over = fake.order_send(
        {
            "action": TRADE_ACTION_DEAL,
            "symbol": "SPY",
            "volume": 2.5,
            "type": ORDER_TYPE_SELL,
            "position": ticket2,
        }
    )
    assert res_over.retcode == TRADE_RETCODE_INVALID_VOLUME
    # Ticket2 remains unchanged
    pos2_lookup = fake.positions_get(ticket=ticket2)
    assert pos2_lookup is not None
    assert pos2_lookup[0].volume == 2.0

    # Close ticket1 exactly
    res_close1 = fake.order_send(
        {
            "action": TRADE_ACTION_DEAL,
            "symbol": "SPY",
            "volume": 1.0,
            "type": ORDER_TYPE_SELL,
            "position": ticket1,
        }
    )
    assert res_close1.retcode == TRADE_RETCODE_DONE
    positions_after = fake.positions_get(symbol="SPY")
    assert positions_after is not None
    assert len(positions_after) == 1
    assert positions_after[0].ticket == ticket2
    assert positions_after[0].volume == 2.0


def test_fake_implements_mt5client_protocol():
    """FakeMT5 must satisfy the MT5Client runtime Protocol."""
    fake = FakeMT5(account_currency="EUR", balance=100000.0)
    assert isinstance(fake, MT5Client)

    # Verify basic Protocol methods
    assert fake.initialize() is True
    assert fake.login(12345678, "pwd", "server") is True
    info = fake.account_info()
    assert info is not None
    assert info.currency == "EUR"
    assert info.balance == 100000.0
    assert fake.symbol_info("SPY") is not None
    assert fake.symbol_select("SPY", True) is True
    assert fake.symbol_info_tick("SPY") is not None
    symbols = fake.symbols_get()
    assert symbols is not None
    assert len(symbols) > 0
    fake.shutdown()


def test_connect_rpyc_returns_remote_metatrader5_module(mocker):
    """connect_rpyc opens a classic RPyC connection and returns conn.modules['MetaTrader5']."""
    import rpyc

    conn = MagicMock()
    remote_mt5 = MagicMock()
    conn.modules.__getitem__.side_effect = lambda name: remote_mt5 if name == "MetaTrader5" else None
    connect = mocker.patch.object(rpyc.classic, "connect", return_value=conn)

    client = connect_rpyc(host="cluster-bridge", port=8001)
    assert client.initialize is remote_mt5.initialize  # attribute access delegates to the remote module
    connect.assert_called_once_with("cluster-bridge", 8001)
