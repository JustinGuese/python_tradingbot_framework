"""Unit tests for MT5Broker using FakeMT5."""

from unittest.mock import MagicMock

import pytest

from tests.mt5_fake import FakeMT5
from tradingbot.livetrade.mt5.broker import MT5Broker
from tradingbot.livetrade.mt5.client import (
    ACCOUNT_MARGIN_MODE_RETAIL_HEDGING,
)
from tradingbot.livetrade.registry import ConfigError


def test_volume_rounding_and_clamping():
    """Volume must round to volume_step, skip if < min, and clamp to max."""
    fake = FakeMT5(symbols={})
    fake.add_symbol(
        name="TEST",
        volume_min=0.10,
        volume_step=0.05,
        volume_max=5.00,
        bid=100.0,
        ask=100.0,
    )
    broker = MT5Broker(fake)

    # 1. Rounding: 0.12 with step 0.05 -> 0.10
    broker.place_order("TEST", 0.12, "BUY")
    pos = broker.get_positions()
    assert pos.get("TEST") == 0.10

    # 2. Skip when below min: 0.04 rounds to 0.00 or 0.05 < min (0.10) -> skipped
    broker.place_order("TEST", 0.04, "BUY")
    pos = broker.get_positions()
    assert pos.get("TEST") == 0.10  # unchanged

    # 3. Clamping to volume_max: 10.0 exceeds max (5.0) -> clamped to 5.0
    broker.place_order("TEST", 10.0, "BUY")
    pos = broker.get_positions()
    assert pos.get("TEST") == pytest.approx(5.10)


def test_market_closed_rejection_raises():
    """Rejection retcode 10018 (market closed) must raise RuntimeError."""
    fake = FakeMT5(market_closed=True)
    broker = MT5Broker(fake)

    with pytest.raises(RuntimeError, match="10018"):
        broker.place_order("SPY", 1.0, "BUY")


def test_eur_account_conversion_and_missing_rate_raises():
    """EUR account converts at provided rate; zero or missing rate raises."""
    fake = FakeMT5(account_currency="EUR", balance=1000.0, equity=1500.0)

    # Injectable rate provider
    broker_injected = MT5Broker(fake, rate_provider=lambda: 1.10)
    assert broker_injected.get_cash() == pytest.approx(1100.0)
    assert broker_injected.get_total_equity() == pytest.approx(1650.0)

    # Missing / zero rate provider must raise ValueError
    broker_zero_rate = MT5Broker(fake, rate_provider=lambda: 0.0)
    with pytest.raises(ValueError, match="positive and non-zero"):
        broker_zero_rate.get_cash()

    broker_none_rate = MT5Broker(fake, rate_provider=lambda: None)  # type: ignore
    with pytest.raises(ValueError, match="positive and non-zero"):
        broker_none_rate.get_cash()

    # DataService fallback
    mock_ds = MagicMock()
    mock_ds.get_latest_price.return_value = 1.05
    broker_ds = MT5Broker(fake, data_service=mock_ds)
    assert broker_ds.get_cash() == pytest.approx(1050.0)
    mock_ds.get_latest_price.assert_called_with("EURUSD=X")

    # DataService returning 0.0 must raise ValueError
    mock_ds.get_latest_price.return_value = 0.0
    with pytest.raises(ValueError, match="positive and non-zero"):
        broker_ds.get_cash()


def test_unsupported_currency_raises():
    """Any currency other than USD or EUR must raise ConfigError; USD passes through."""
    fake_gbp = FakeMT5(account_currency="GBP", balance=1000.0, equity=1000.0)
    broker_gbp = MT5Broker(fake_gbp)
    with pytest.raises(ConfigError, match="Unsupported MT5 account currency"):
        broker_gbp.get_cash()
    with pytest.raises(ConfigError, match="Unsupported MT5 account currency"):
        broker_gbp.get_total_equity()

    # USD passes through 1:1
    fake_usd = FakeMT5(account_currency="USD", balance=2500.0, equity=3000.0)
    broker_usd = MT5Broker(fake_usd)
    assert broker_usd.get_cash() == 2500.0
    assert broker_usd.get_total_equity() == 3000.0


def test_hedged_tickets_net_and_sell_does_not_overshoot():
    """Hedged tickets net correctly, SELL closes by ticket, and never oversells."""
    fake = FakeMT5(margin_mode=ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
    t1 = fake.add_position("SPY", volume=1.0)
    t2 = fake.add_position("SPY", volume=2.0)

    broker = MT5Broker(fake)
    # Net volume of 1.0 + 2.0 = 3.0
    assert broker.get_positions() == {"SPY": 3.0}

    # Partial SELL of 1.5: closes t1 completely (1.0), reduces t2 by 0.5 (leaving 1.5)
    broker.place_order("SPY", 1.5, "SELL")
    assert broker.get_positions() == {"SPY": 1.5}
    assert fake.positions_get(ticket=t1.ticket) == ()
    rem_pos = fake.positions_get(ticket=t2.ticket)
    assert rem_pos is not None and len(rem_pos) == 1
    assert rem_pos[0].volume == pytest.approx(1.5)

    # Oversell attempt: ask to sell 10.0 when only 1.5 remains
    # Must clamp to 1.5 and not create any short position
    broker.place_order("SPY", 10.0, "SELL")
    assert broker.get_positions() == {}
    assert fake.positions_get(symbol="SPY") == ()


def test_symbol_resolves_to_suffixed_when_bare_does_not_exist():
    """Symbol resolution finds suffixed symbol and rejects ambiguous candidates."""
    fake = FakeMT5(symbols={})
    fake.add_symbol("SPY.P", volume_min=0.01, volume_step=0.01, volume_max=100.0)
    broker = MT5Broker(fake)

    assert broker.is_tradeable("SPY") is True
    mapped = broker.map_symbol("SPY")
    assert mapped is not None
    assert mapped["symbol"] == "SPY.P"
    assert fake.symbol_info("SPY.P").select is True

    # Ambiguous: both QQQ.US and QQQ.P exist
    fake.add_symbol("QQQ.US")
    fake.add_symbol("QQQ.P")
    assert broker.is_tradeable("QQQ") is False
    assert broker.map_symbol("QQQ") is None


def test_cancel_open_orders_and_cash_proxy():
    """cancel_open_orders removes pending orders; cash_proxy is None unless SHV listed."""
    fake = FakeMT5(symbols={})
    broker_no_shv = MT5Broker(fake)
    assert broker_no_shv.cash_proxy is None

    fake.add_symbol("SHV")
    broker_with_shv = MT5Broker(fake)
    assert broker_with_shv.cash_proxy == "SHV"

    # Pending order cancellation
    from tradingbot.livetrade.mt5.client import TRADE_ACTION_PENDING

    fake.order_send(
        {
            "action": TRADE_ACTION_PENDING,
            "symbol": "SHV",
            "volume": 1.0,
            "price": 110.0,
        }
    )
    assert len(fake.orders_get() or ()) == 1
    cancelled = broker_with_shv.cancel_open_orders()
    assert cancelled == 1
    assert len(fake.orders_get() or ()) == 0


def test_contract_size_other_than_one_is_refused():
    """Copier quantities are shares; MT5 volume is lots. A contract size != 1 would mis-size
    the order, so it must raise instead of trading."""
    from types import SimpleNamespace

    fake = FakeMT5(symbols={})
    fake.add_symbol(name="FXLIKE", bid=1.1, ask=1.1)
    broker = MT5Broker(fake)
    real = fake.symbol_info("FXLIKE")
    fake.symbol_info = lambda s: SimpleNamespace(**real._asdict(), trade_contract_size=100000.0)  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="contract size"):
        broker.place_order("FXLIKE", 10, "BUY")
    assert broker.get_positions() == {}


def test_failed_order_listing_or_cancel_aborts_instead_of_returning_zero():
    """A failed orders_get (None) or an order that will not cancel must raise; returning 0
    would let the copier size new orders on top of working ones."""
    fake = FakeMT5(symbols={})
    broker = MT5Broker(fake)

    fake.orders_get = lambda *a, **k: None  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="orders_get failed"):
        broker.cancel_open_orders()

    fake.orders_get = lambda *a, **k: (MagicMock(ticket=7),)  # type: ignore[method-assign]
    fake.order_send = lambda req: MagicMock(retcode=10013, comment="invalid request")  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="failed to cancel order 7"):
        broker.cancel_open_orders()


def test_symbol_index_uses_one_bulk_call_and_is_cached():
    """Resolving many symbols must not re-list (or walk, over RPC) the broker's symbols each time."""
    from unittest.mock import MagicMock

    from tradingbot.livetrade.mt5.broker import MT5Broker

    client = MagicMock()
    client.symbol_names.return_value = ["SPY.US", "TLT.US", "EURUSD"]
    broker = MT5Broker.__new__(MT5Broker)
    broker.client = client
    broker.symbol_mapper = None
    broker.name = "mt5_mql5"

    assert broker._resolve_symbol("SPY") == "SPY.US"
    assert broker._resolve_symbol("TLT") == "TLT.US"
    assert broker._resolve_symbol("EURUSD=X") == "EURUSD"
    assert broker._resolve_symbol("NOPE") is None
    client.symbol_names.assert_called_once()
    client.symbols_get.assert_not_called()


def test_timeout_followed_by_position_present_succeeds_idempotent():
    """Timeout during order_send followed by confirmed position must succeed without resend."""
    fake = FakeMT5()
    broker = MT5Broker(fake)

    # 1. RPyC timeout exception with fill: order_send raises "result expired",
    # but the position is filled on the terminal side.
    fake.timeout_on_send = True
    fake.timeout_raises = True
    fake.fill_on_timeout = True

    broker.place_order("SPY", 1.0, "BUY")
    assert broker.get_positions() == {"SPY": 1.0}

    # 2. Server-side retcode timeout (10012) with fill: position volume increments.
    fake.timeout_raises = False
    broker.place_order("SPY", 1.0, "BUY")
    assert broker.get_positions() == {"SPY": 2.0}


def test_timeout_with_no_position_raises():
    """Timeout without position update must raise RuntimeError and leave positions empty."""
    fake = FakeMT5()
    broker = MT5Broker(fake)

    fake.timeout_on_send = True
    fake.fill_on_timeout = False

    with pytest.raises(RuntimeError, match="timed out"):
        broker.place_order("SPY", 1.0, "BUY")

    assert "SPY" not in broker.get_positions()


def test_sell_timeout_confirmed_via_position():
    """SELL order timeout confirmed by ticket closure must succeed."""
    fake = FakeMT5()
    fake.add_position("SPY", volume=2.0)
    broker = MT5Broker(fake)
    assert broker.get_positions() == {"SPY": 2.0}

    fake.timeout_on_send = True
    fake.timeout_raises = True
    fake.fill_on_timeout = True

    broker.place_order("SPY", 1.0, "SELL")
    assert broker.get_positions() == {"SPY": 1.0}


def test_retcode_mapping_and_order_results():
    """retcodes map to human-readable strings; DONE_PARTIAL and PLACED succeed; rejections describe retcode."""
    from tradingbot.livetrade.mt5.client import (
        TRADE_RETCODE_DONE,
        TRADE_RETCODE_DONE_PARTIAL,
        TRADE_RETCODE_NO_MONEY,
        TRADE_RETCODE_PLACED,
        TRADE_RETCODE_TIMEOUT,
        OrderSendResult,
        describe_retcode,
    )

    assert describe_retcode(TRADE_RETCODE_DONE) == "Request completed"
    assert describe_retcode(TRADE_RETCODE_DONE_PARTIAL) == "Only part of request completed"
    assert describe_retcode(TRADE_RETCODE_TIMEOUT) == "Request canceled by timeout"
    assert describe_retcode(TRADE_RETCODE_NO_MONEY) == "Not enough money"

    fake = FakeMT5()
    broker = MT5Broker(fake)

    # Partial fill is considered success
    fake.order_send = lambda req: OrderSendResult(
        retcode=TRADE_RETCODE_DONE_PARTIAL, volume=1.0, comment="Partial fill"
    )
    broker.place_order("SPY", 1.0, "BUY")

    # Order placed (e.g. pending limit) is considered success
    fake.order_send = lambda req: OrderSendResult(retcode=TRADE_RETCODE_PLACED, volume=1.0, comment="Placed")
    broker.place_order("SPY", 1.0, "BUY")

    # Rejected order with retcode description in error message
    fake.order_send = lambda req: OrderSendResult(retcode=TRADE_RETCODE_NO_MONEY, comment="Margin required")
    with pytest.raises(RuntimeError, match=r"10019 \(Not enough money\)"):
        broker.place_order("SPY", 1.0, "BUY")


def test_supports_limit_orders_and_deviation():
    """MT5Broker advertises supports_limit_orders and calculates deviation for marketable limits."""
    fake = FakeMT5()
    broker = MT5Broker(fake)

    assert broker.supports_limit_orders is True

    # SPY ask is 500.0, limit_price is 500.75 -> point is 0.01 -> 75 points diff
    broker.place_order("SPY", 1.0, "BUY", limit_price=500.75)
    last_req = fake.last_order_request
    assert last_req is not None
    assert last_req["deviation"] >= 75
    assert last_req["price"] == pytest.approx(500.0)


def test_filling_mode_prefers_ioc():
    """Filling mode prefers IOC (1) when supported, FOK (0) when only FOK, else RETURN (2)."""
    from tradingbot.livetrade.mt5.client import (
        ORDER_FILLING_FOK,
        ORDER_FILLING_IOC,
        ORDER_FILLING_RETURN,
        SymbolInfo,
    )

    broker = MT5Broker(FakeMT5())

    # IOC and FOK supported (bitmask 3) -> IOC preferred
    sym_both = SymbolInfo(filling_mode=3)
    assert broker._determine_filling_mode(sym_both) == ORDER_FILLING_IOC

    # Only IOC supported (bitmask 2) -> IOC
    sym_ioc = SymbolInfo(filling_mode=2)
    assert broker._determine_filling_mode(sym_ioc) == ORDER_FILLING_IOC

    # Only FOK supported (bitmask 1) -> FOK
    sym_fok = SymbolInfo(filling_mode=1)
    assert broker._determine_filling_mode(sym_fok) == ORDER_FILLING_FOK

    # Neither supported (bitmask 0) -> RETURN
    sym_none = SymbolInfo(filling_mode=0)
    assert broker._determine_filling_mode(sym_none) == ORDER_FILLING_RETURN


@pytest.mark.parametrize(
    ("trade_mode", "expected"),
    [(0, False), (1, True), (2, False), (3, False), (4, True)],
)
def test_is_tradeable_honours_server_trade_mode(trade_mode, expected):
    """A listed symbol the server has disabled (MetaQuotes-Demo: SPY, UUP) is not tradeable.

    Otherwise the copier submits the buy every run and gets retcode 10017.
    """
    fake = FakeMT5(symbols={})
    fake.add_symbol("SPY", trade_mode=trade_mode)
    broker = MT5Broker(fake)

    assert broker.is_tradeable("SPY") is expected


def test_is_tradeable_false_when_symbol_info_unavailable():
    fake = FakeMT5(symbols={})
    fake.add_symbol("SPY")
    broker = MT5Broker(fake)
    fake.symbol_info = lambda name: None  # type: ignore[method-assign]

    assert broker.is_tradeable("SPY") is False
