"""
Paper options: a bot says buy("AAPL", option=True) and never sees a contract.

yfinance is faked with the real shapes it returns: `Ticker.options` is a tuple of
ISO dates, `option_chain(date)` a namedtuple of (calls, puts, underlying) whose
frames carry contractSymbol/strike/bid/ask/lastPrice/....
"""

from collections import namedtuple
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tradingbot.livetrade.broker import LiveBroker
from tradingbot.livetrade.copier import LiveTradeCopier
from tradingbot.utils import options
from tradingbot.utils.bot_repository import BotRepository
from tradingbot.utils.botclass import Bot
from tradingbot.utils.config import ExecutionConfig
from tradingbot.utils.db import Bot as BotModel
from tradingbot.utils.db import OptionQuote, Trade
from tradingbot.utils.portfolio_manager import PortfolioManager

TODAY = options.utc_today()
NEAR = TODAY + timedelta(days=3)  # inside the 7-day roll window
MID = TODAY + timedelta(days=12)  # too close for a 30-DTE buy
FAR = TODAY + timedelta(days=35)  # first expiry >= 30 DTE
SPOT = 201.0
NO_COSTS = ExecutionConfig(slippage_pct=0.0, commission_pct=0.0, option_slippage_pct=0.02)

Options = namedtuple("Options", ["calls", "puts", "underlying"])


def occ(root: str, expiry: date, right: str, strike: float) -> str:
    return f"{root}{expiry:%y%m%d}{right}{round(strike * 1000):08d}"


def _side(
    expiry: date, right: str, zero_bid_strike: float | None = None, only_quoted: float | None = None
) -> pd.DataFrame:
    rows = []
    for strike in (190.0, 200.0, 210.0):
        unquoted = strike == zero_bid_strike or (only_quoted is not None and strike != only_quoted)
        bid, ask = (0.0, 0.0) if unquoted else (4.90, 5.10)
        rows.append(
            {
                "contractSymbol": occ("AAPL", expiry, right, strike),
                "strike": strike,
                "lastPrice": 5.0,
                "bid": bid,
                "ask": ask,
                "volume": 10.0,
                "openInterest": 100.0,
                "impliedVolatility": 0.3,
            }
        )
    return pd.DataFrame(rows)


class FakeTicker:
    zero_bid_strike: float | None = None
    only_quoted: float | None = None
    market_state = "REGULAR"
    close = 200.0

    def __init__(self, symbol):
        self.symbol = symbol
        self.options = tuple(d.isoformat() for d in (NEAR, MID, FAR))

    def option_chain(self, expiry):
        exp = date.fromisoformat(expiry)
        return Options(
            calls=_side(exp, "C", self.zero_bid_strike, self.only_quoted),
            puts=_side(exp, "P", self.zero_bid_strike, self.only_quoted),
            underlying={"regularMarketPrice": SPOT, "marketState": self.market_state},
        )

    def history(self, **_):
        return pd.DataFrame({"Close": [self.close]})


@pytest.fixture
def fake_yf(monkeypatch):
    FakeTicker.zero_bid_strike = None
    FakeTicker.only_quoted = None
    FakeTicker.market_state = "REGULAR"
    FakeTicker.close = 200.0
    monkeypatch.setattr(options.yf, "Ticker", FakeTicker)
    return FakeTicker


@pytest.fixture
def pm(sqlite_db, db_session, test_bot, fake_yf):
    ds = MagicMock()
    ds.get_latest_price.side_effect = lambda sym, *a, **k: (
        options.option_price(sym) if options.is_option_symbol(sym) else SPOT
    )
    return PortfolioManager(test_bot, test_bot.name, ds, BotRepository, execution_config=NO_COSTS)


def _portfolio(db_session, name="TestBot") -> dict:
    db_session.expire_all()
    return dict(db_session.query(BotModel).filter_by(name=name).one().portfolio)


def _set_portfolio(db_session, portfolio, name="TestBot"):
    bot = db_session.query(BotModel).filter_by(name=name).one()
    bot.portfolio = portfolio
    db_session.commit()


# ------------------------------------------------------------------
# OCC helpers
# ------------------------------------------------------------------


def test_parse_occ_real_yfinance_symbols():
    c = options.parse_occ("AAPL251017C00150000")
    assert (c.underlying, c.expiry, c.right, c.strike) == ("AAPL", date(2025, 10, 17), "C", 150.0)
    assert options.parse_occ("SPY251017P00587500").strike == 587.5


@pytest.mark.parametrize("symbol", ["AAPL", "EURUSD=X", "^XAU", "BTC-USD", "GC=F", "RENW.DE", ""])
def test_non_options_are_not_option_symbols(symbol):
    assert not options.is_option_symbol(symbol)


def test_whole_contract_qty_floors_and_absorbs_float_residue():
    assert options.whole_contract_qty(250.0) == 200
    assert options.whole_contract_qty(299.99999999999) == 300
    assert options.whole_contract_qty(99.0) == 0


def test_normalize_right():
    assert options.normalize_right(True) == "C"
    assert options.normalize_right("put") == "P"
    assert options.normalize_right(None) is None
    assert options.normalize_right(False) is None
    with pytest.raises(ValueError):
        options.normalize_right("straddle")


# ------------------------------------------------------------------
# Contract selection
# ------------------------------------------------------------------


def test_select_contract_first_expiry_past_target_and_atm_strike(sqlite_db, db_session, fake_yf):
    chosen = options.select_contract("AAPL", "C", target_dte=30, spot=SPOT)
    assert chosen == occ("AAPL", FAR, "C", 200.0)
    # The snapshot was stored: both sides, three strikes.
    assert db_session.query(OptionQuote).count() == 6


def test_select_contract_skips_contracts_without_a_market(sqlite_db, fake_yf):
    fake_yf.zero_bid_strike = 200.0
    # 210 (9 away) beats 190 (11 away) once the ATM 200 has no bid/ask.
    assert options.select_contract("AAPL", "P", target_dte=30, spot=SPOT) == occ("AAPL", FAR, "P", 210.0)


def test_off_hours_stale_quotes_are_ignored(sqlite_db, db_session, fake_yf):
    """Pre-market, yfinance zeroes bid/ask on nearly the whole chain but leaves a
    few stale remnants. Preferring those picked a contract far from the money."""
    fake_yf.market_state = "PRE"
    fake_yf.only_quoted = 190.0  # the lone stale remnant
    assert options.select_contract("AAPL", "C", target_dte=30, spot=SPOT) == occ("AAPL", FAR, "C", 200.0)
    assert db_session.query(OptionQuote).filter(OptionQuote.bid.isnot(None)).count() == 0


def test_off_hours_buy_fills_at_last_plus_option_slippage(pm, db_session, fake_yf):
    fake_yf.market_state = "CLOSED"
    pm.buy("AAPL", quantity_usd=5000.0, option=True)
    # No live bid/ask: last 5.00 * 1.02 = 5.10 -> 980 -> 900 share-equivalents.
    assert _portfolio(db_session)[occ("AAPL", FAR, "C", 200.0)] == 900
    assert db_session.query(Trade).one().price == pytest.approx(5.10)


# ------------------------------------------------------------------
# PortfolioManager
# ------------------------------------------------------------------


def test_buy_option_by_underlying_whole_contracts_at_the_ask(pm, db_session):
    pm.buy("AAPL", quantity_usd=5000.0, option=True)

    contract = occ("AAPL", FAR, "C", 200.0)
    book = _portfolio(db_session)
    # 5000 / 5.10 = 980 share-equivalents -> 9 whole contracts = 900.
    assert book[contract] == 900
    assert book["USD"] == pytest.approx(10000.0 - 900 * 5.10)  # change stays cash
    trade = db_session.query(Trade).one()
    assert (trade.symbol, trade.price, trade.isBuy) == (contract, 5.10, True)


def test_buy_below_one_contract_is_skipped(pm, db_session):
    pm.buy("AAPL", quantity_usd=400.0, option="put")  # one contract costs $510
    assert _portfolio(db_session) == {"USD": 10000.0}


def test_plain_buy_still_buys_stock(pm, db_session):
    pm.buy("AAPL", quantity_usd=2010.0)
    assert _portfolio(db_session)["AAPL"] == pytest.approx(10.0)


def test_raw_contract_symbol_cannot_be_bought(pm):
    with pytest.raises(ValueError, match="by underlying"):
        pm.buy(occ("AAPL", FAR, "C", 200.0), quantity_usd=1000.0)


def test_rebalance_rejects_contract_targets(pm):
    with pytest.raises(ValueError, match="cannot target option contracts"):
        pm.rebalance_portfolio({occ("AAPL", FAR, "C", 200.0): 0.5, "USD": 0.5})


def test_sell_option_by_underlying_closes_all_at_the_bid(pm, db_session):
    call, put = occ("AAPL", FAR, "C", 200.0), occ("AAPL", FAR, "P", 190.0)
    _set_portfolio(db_session, {"USD": 0.0, call: 200.0, put: 100.0, "MSFT": 1.0})

    proceeds = pm.sell("AAPL", option=True)

    assert proceeds == pytest.approx(300 * 4.90)
    assert _portfolio(db_session) == {"USD": pytest.approx(300 * 4.90), "MSFT": 1.0}


def test_sell_option_side_filter(pm, db_session):
    call, put = occ("AAPL", FAR, "C", 200.0), occ("AAPL", FAR, "P", 190.0)
    _set_portfolio(db_session, {"USD": 0.0, call: 200.0, put: 100.0})
    pm.sell("AAPL", option="put")
    assert set(_portfolio(db_session)) == {"USD", call}


def test_roll_swaps_a_near_expiry_contract_into_a_fresh_one(pm, db_session):
    near = occ("AAPL", NEAR, "C", 200.0)
    _set_portfolio(db_session, {"USD": 0.0, near: 300.0})

    pm.roll_and_settle_options(roll_dte=7, target_dte=30)

    book = _portfolio(db_session)
    assert near not in book
    # 300 * 4.90 = 1470 proceeds / 5.10 ask = 288 -> 2 contracts.
    assert book[occ("AAPL", FAR, "C", 200.0)] == 200
    assert book["USD"] == pytest.approx(1470.0 - 200 * 5.10)


@pytest.mark.parametrize(("right", "expected_value"), [("C", 10.0), ("P", 0.0)])
def test_expired_contract_settles_at_intrinsic(pm, db_session, fake_yf, right, expected_value):
    expired = occ("AAPL", TODAY - timedelta(days=1), right, 190.0)
    fake_yf.close = 200.0  # call 190 is $10 in the money, put 190 worthless
    _set_portfolio(db_session, {"USD": 0.0, expired: 200.0})

    pm.roll_and_settle_options(roll_dte=7, target_dte=30)

    assert _portfolio(db_session) == {"USD": pytest.approx(200 * expected_value)}


def test_roll_is_a_noop_without_options(pm, db_session):
    _set_portfolio(db_session, {"USD": 100.0, "AAPL": 1.0})
    pm.roll_and_settle_options(roll_dte=7, target_dte=30)
    assert _portfolio(db_session) == {"USD": 100.0, "AAPL": 1.0}


# ------------------------------------------------------------------
# Bot opt-in
# ------------------------------------------------------------------


class _CallsBot(Bot):
    USE_OPTIONS = True

    def decisionFunction(self, row):
        return 1


def test_use_options_routes_default_buys_through_contracts(mocker):
    mocker.patch("tradingbot.utils.botclass.init_db")
    mocker.patch("tradingbot.utils.botclass.BotRepository.create_or_get_bot")
    bot = _CallsBot("CallsBot", symbol="AAPL", interval="1d", period="1y")
    bot._portfolio_manager = MagicMock()

    bot.buy("AAPL")
    bot.buy("QQQ", option=False)  # explicit argument beats the class flag

    calls = bot._portfolio_manager.buy.call_args_list
    assert calls[0].kwargs["option"] is True
    assert calls[0].kwargs["target_dte"] == 30
    assert calls[1].kwargs["option"] is False


def test_options_are_off_by_default(mocker):
    mocker.patch("tradingbot.utils.botclass.init_db")
    mocker.patch("tradingbot.utils.botclass.BotRepository.create_or_get_bot")
    bot = Bot("Plain", symbol="AAPL")
    bot._portfolio_manager = MagicMock()
    bot.buy("AAPL")
    assert bot._portfolio_manager.buy.call_args.kwargs["option"] is False


def test_use_options_refuses_multi_ticker_bots(mocker):
    mocker.patch("tradingbot.utils.botclass.init_db")
    create = mocker.patch("tradingbot.utils.botclass.BotRepository.create_or_get_bot")
    with pytest.raises(ValueError, match="USE_OPTIONS"):
        _CallsBot("Multi", tickers=["AAPL", "MSFT"])
    create.assert_not_called()


# ------------------------------------------------------------------
# Live copier: contracts never reach a broker
# ------------------------------------------------------------------


def test_copier_drops_option_holdings_even_under_strict_mapping():
    broker = MagicMock(spec=LiveBroker)
    broker.name = "mock_broker"
    broker.is_tradeable.return_value = True  # the default a real broker gives an OCC symbol
    broker.map_symbol.side_effect = lambda s: {"symbol": s, "type": "stock"}
    broker.get_total_equity.return_value = 1000.0
    broker.get_positions.return_value = {}
    broker.cancel_open_orders.return_value = 0

    copier = LiveTradeCopier(broker=broker, bot_weights={"bot1": 1.0}, dry_run=True)
    copier.strict_mapping = True
    copier.cash_proxy = None
    contract = occ("AAPL", FAR, "C", 200.0)
    bot = MagicMock(spec=BotModel)
    bot.portfolio = {"USD": 0, "QQQ": 1.0, contract: 100.0}
    copier.bot_repo = MagicMock()
    copier.bot_repo.create_or_get_bot.return_value = bot
    copier.data_service = MagicMock()
    copier.data_service.get_latest_prices_batch.return_value = {"QQQ": 500.0, contract: 5.0}

    with patch.object(copier, "_calculate_orders", return_value=[]) as calc:
        copier.sync()

    assert set(calc.call_args.args[0]) == {"QQQ"}
    broker.map_symbol.assert_called_once_with("QQQ")
