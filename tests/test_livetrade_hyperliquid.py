import logging
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tradingbot.livetrade.broker import LiveBroker
from tradingbot.livetrade.copier import LiveTradeCopier
from tradingbot.livetrade.hyperliquid import HyperliquidBroker
from tradingbot.utils.db import Base, LiveEquity

# Syntactically valid secp256k1 key so eth_account.Account.from_key() succeeds.
DUMMY_KEY = "0x" + "11" * 32
VAULT = "0x" + "aa" * 20
ACCOUNT = "0x" + "bb" * 20

FLAT_STATE = {
    "marginSummary": {
        "accountValue": "1000.0",
        "totalNtlPos": "0.0",
        "totalRawUsd": "1000.0",
        "totalMarginUsed": "0.0",
    },
    "withdrawable": "1000.0",
    "assetPositions": [],
}


def _make(info=None, exchange=None, **kwargs):
    info = info or MagicMock()
    info.meta.return_value = {
        "universe": [
            {"name": "BTC", "szDecimals": 5},
            {"name": "ETH", "szDecimals": 4},
            {"name": "SOL", "szDecimals": 2},
        ]
    }
    info.all_mids.return_value = {"BTC": "100000.0", "ETH": "3000.0", "SOL": "200.0"}
    info.user_state.return_value = FLAT_STATE
    info.open_orders.return_value = []
    kwargs.setdefault("vault_address", VAULT)
    return HyperliquidBroker(
        private_key=DUMMY_KEY,
        account_address=ACCOUNT,
        testnet=True,
        data_service=MagicMock(),
        info=info,
        exchange=exchange or MagicMock(),
        **kwargs,
    )


def _with_position(broker, coin, szi):
    broker.info.user_state.return_value = {
        **FLAT_STATE,
        "assetPositions": [{"position": {"coin": coin, "szi": str(szi)}}],
    }


@pytest.fixture
def hl():
    return _make()


# --------------------------------------------------------------- account state


def test_equity_parses_string_account_value(hl):
    assert hl.get_total_equity() == 1000.0


def test_get_cash_returns_withdrawable(hl):
    hl.info.user_state.return_value = {**FLAT_STATE, "withdrawable": "250.5"}
    assert hl.get_cash() == 250.5


def test_get_cash_account_value_mode():
    broker = _make(cash_mode="account_value")
    broker.info.user_state.return_value = {**FLAT_STATE, "withdrawable": "250.5"}
    assert broker.get_cash() == 1000.0


def test_get_cash_returns_zero_on_error(hl):
    """Zero makes the copier skip all buys — the safe direction (ends flatter)."""
    hl.info.user_state.side_effect = RuntimeError("api down")
    assert hl.get_cash() == 0.0


def test_reads_use_vault_address_not_account(hl):
    """The single most important test: sizing against the leader's personal
    balance instead of the vault's would mis-size every order."""
    hl.get_total_equity()
    hl.info.user_state.assert_called_with(VAULT)

    hl.get_positions()
    hl.info.user_state.assert_called_with(VAULT)

    hl.cancel_open_orders()
    hl.info.open_orders.assert_called_with(VAULT)


def test_reads_fall_back_to_account_when_no_vault():
    broker = _make(vault_address=None)
    broker.get_total_equity()
    broker.info.user_state.assert_called_with(ACCOUNT)


def test_get_positions_signed_and_skips_zero(hl):
    hl.info.user_state.return_value = {
        **FLAT_STATE,
        "assetPositions": [
            {"position": {"coin": "BTC", "szi": "0.01"}},
            {"position": {"coin": "ETH", "szi": "-0.5"}},
            {"position": {"coin": "SOL", "szi": "0.0"}},
        ],
    }
    assert hl.get_positions() == {"BTC": 0.01, "ETH": -0.5}


# ---------------------------------------------------------------------- orders


def test_place_order_buy_rounds_size_to_sz_decimals(hl):
    hl.place_order("BTC", 0.0123456789, "BUY")
    hl.exchange.market_open.assert_called_once_with(
        "BTC", is_buy=True, sz=0.01235, slippage=0.02
    )


def test_place_order_sell_on_long_uses_market_close(hl):
    _with_position(hl, "BTC", 0.02)
    hl.place_order("BTC", 0.01, "SELL")
    hl.exchange.market_close.assert_called_once_with("BTC", sz=0.01, slippage=0.02)
    hl.exchange.market_open.assert_not_called()


def test_sell_larger_than_position_is_clamped_never_shorts(hl):
    _with_position(hl, "BTC", 0.01)
    hl.place_order("BTC", 0.05, "SELL")
    hl.exchange.market_close.assert_called_once_with("BTC", sz=0.01, slippage=0.02)


def test_sell_with_no_position_is_refused(hl):
    hl.place_order("BTC", 0.01, "SELL")
    hl.exchange.market_close.assert_not_called()
    hl.exchange.market_open.assert_not_called()


def test_below_ten_dollar_minimum_is_skipped(hl):
    hl.place_order("BTC", 0.00005, "BUY")  # $5 notional
    hl.exchange.market_open.assert_not_called()


def test_exact_close_below_minimum_is_allowed(hl):
    """A dust position must still be closable — HL exempts exact reduce-only."""
    _with_position(hl, "BTC", 0.00005)
    hl.place_order("BTC", 0.00005, "SELL")
    hl.exchange.market_close.assert_called_once_with("BTC", sz=0.00005, slippage=0.02)


def test_partial_sell_below_minimum_is_skipped(hl):
    _with_position(hl, "BTC", 0.01)
    hl.place_order("BTC", 0.00005, "SELL")  # $5 partial reduce
    hl.exchange.market_close.assert_not_called()


def test_size_rounding_to_zero_skips(hl):
    hl.place_order("SOL", 0.004, "BUY")  # SOL szDecimals=2 -> 0.0
    hl.exchange.market_open.assert_not_called()


def test_unlisted_coin_is_refused(hl):
    hl.place_order("QQQ", 10, "BUY")
    hl.exchange.market_open.assert_not_called()


def test_leverage_set_once_before_first_open(hl):
    hl.place_order("BTC", 0.01, "BUY")
    hl.place_order("BTC", 0.01, "BUY")
    hl.exchange.update_leverage.assert_called_once_with(1, "BTC", is_cross=True)


def test_leverage_not_set_for_pure_reduce(hl):
    _with_position(hl, "BTC", 0.02)
    hl.place_order("BTC", 0.01, "SELL")
    hl.exchange.update_leverage.assert_not_called()


def test_error_response_is_logged_not_swallowed(hl, caplog):
    """Hyperliquid returns rejections inside a 200 OK."""
    hl.exchange.market_open.return_value = {
        "status": "ok",
        "response": {
            "data": {"statuses": [{"error": "Order must have minimum value of 10"}]}
        },
    }
    with caplog.at_level(logging.ERROR):
        hl.place_order("BTC", 0.01, "BUY")
    assert "minimum value of 10" in caplog.text


def test_cancel_open_orders(hl):
    hl.info.open_orders.return_value = [
        {"coin": "BTC", "oid": 1},
        {"coin": "ETH", "oid": 2},
    ]
    assert hl.cancel_open_orders() == 2
    assert hl.exchange.cancel.call_count == 2


# --------------------------------------------------------------------- symbols


def test_native_price_and_yf_fallback(hl):
    assert hl._get_native_price("BTC") == 100000.0
    assert hl._get_native_price("DOGE") == 0.0

    hl.data_service.get_latest_price.return_value = 0.42
    assert hl.get_latest_price("DOGE") == 0.42


def test_map_symbol(hl):
    assert hl.map_symbol("BTC-USD")["symbol"] == "BTC"
    # SymbolMapper's default rule passes QQQ through as a stock; the universe
    # check is what rejects it, which is what STRICT_MAPPING aborts on.
    assert hl.map_symbol("QQQ") is None
    assert hl.map_symbol("DOGE-USD") is None


def test_search_symbol(hl):
    assert [c["symbol"] for c in hl.search_symbol("ETH")] == ["ETH"]


# ---------------------------------------------------------- copier integration


def test_copier_flattens_a_stray_short():
    """Regression lock on get_positions() returning SIGNED sizes.

    With abs() this takes the copier's full-liquidation branch and emits
    SELL 0.01, doubling the short to -0.02 — every single run. Signed falls
    through to the general diff and buys the short back.
    """
    broker = MagicMock(spec=LiveBroker)
    broker.name = "hyperliquid"
    broker.get_latest_price.return_value = 100000.0

    copier = LiveTradeCopier(broker=broker, bot_weights={"AnyBot": 1.0}, dry_run=True)
    copier.bot_repo = MagicMock()
    copier.data_service = MagicMock()

    orders = copier._calculate_orders({}, {"BTC": -0.01}, 1000.0)

    assert len(orders) == 1
    assert orders[0]["side"] == "BUY"
    assert orders[0]["quantity"] == pytest.approx(0.01)


# -------------------------------------------------------------- equity recorder


@pytest.fixture
def equity_db():
    """In-memory live_equity, patched into equity_recorder's session factory."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    @contextmanager
    def _session():
        yield session
        session.commit()

    with patch("tradingbot.livetrade.equity_recorder.get_db_session", _session), \
         patch("tradingbot.livetrade.equity_recorder.init_db"):
        yield session
    session.close()


def test_records_equity_snapshot(equity_db, hl):
    from tradingbot.livetrade.equity_recorder import record_live_equity

    _with_position(hl, "BTC", 0.01)
    record_live_equity(hl, {"AdaptiveMeanReversionBTCBot": 1.0})

    row = equity_db.query(LiveEquity).one()
    assert row.broker == "hyperliquid"
    assert row.account_id == VAULT  # the vault, not the signing wallet
    assert row.equity == 1000.0
    assert row.positions == {"BTC": 0.01}
    assert row.is_testnet is True


def test_equity_snapshot_is_idempotent_per_day(equity_db, hl):
    from tradingbot.livetrade.equity_recorder import record_live_equity

    record_live_equity(hl)
    hl.info.user_state.return_value = {
        **FLAT_STATE,
        "marginSummary": {**FLAT_STATE["marginSummary"], "accountValue": "1100.0"},
    }
    record_live_equity(hl)

    row = equity_db.query(LiveEquity).one()  # one row, not two
    assert row.equity == 1100.0  # last write wins


def test_zero_equity_is_not_recorded(equity_db, hl):
    """Every adapter returns 0.0 when its API is down. Writing that would punch
    a fake 100% drawdown into the published curve."""
    from tradingbot.livetrade.equity_recorder import record_live_equity

    hl.info.user_state.side_effect = RuntimeError("api down")
    record_live_equity(hl)

    assert equity_db.query(LiveEquity).count() == 0
