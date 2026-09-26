"""
Short option legs, multi-leg structures, and the option_* bots.

The fake chain is priced with Black-Scholes at one volatility, so the IV the
framework solves back out of every mid is exactly SIG and every delta is
predictable. Bid/ask sit 5 cents either side of the model price.
"""

import math
from datetime import timedelta
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from tradingbot.livetrade.copier import LiveTradeCopier
from tradingbot.option_catalystcallbot import OptionCatalystCallBot
from tradingbot.option_collarbot import OptionCollarBot
from tradingbot.option_creditspreadbot import OptionCreditSpreadBot
from tradingbot.option_earningscalendarbot import OptionEarningsCalendarBot
from tradingbot.option_indexvolbot import OptionIndexVolBot
from tradingbot.option_ironcondorbot import OptionIronCondorBot
from tradingbot.option_leapcallbot import OptionLeapCallBot
from tradingbot.option_mispricingbot import OptionMispricingBot
from tradingbot.option_pmccbot import OptionPMCCBot
from tradingbot.option_wheelbot import OptionWheelBot
from tradingbot.utils import option_math as om
from tradingbot.utils import option_rules as rules_mod
from tradingbot.utils import options
from tradingbot.utils.bot_repository import BotRepository
from tradingbot.utils.config import ExecutionConfig
from tradingbot.utils.db import Bot as BotModel
from tradingbot.utils.db import Trade
from tradingbot.utils.portfolio_manager import PortfolioManager
from tradingbot.utils.portfolio_utils import calculate_portfolio_worth

S, R, SIG = 300.0, 0.04, 0.25
TODAY = options.utc_today()
NEAR = TODAY + timedelta(days=3)
E40 = TODAY + timedelta(days=40)
E75 = TODAY + timedelta(days=75)
E560 = TODAY + timedelta(days=560)
STRIKES = [float(k) for k in range(200, 425, 5)]
NO_COSTS = ExecutionConfig(slippage_pct=0.0, commission_pct=0.0, option_slippage_pct=0.02)


def occ(expiry, right, strike):
    return f"AAPL{expiry:%y%m%d}{right}{round(strike * 1000):08d}"


class BSTicker:
    market_state = "REGULAR"
    close = S
    earnings = None

    def __init__(self, symbol):
        self.symbol = symbol
        self.options = tuple(d.isoformat() for d in (NEAR, E40, E75, E560))

    def option_chain(self, expiry):
        exp = pd.Timestamp(expiry).date()
        T = om.year_fraction(exp, TODAY)

        def side(right):
            rows = []
            for k in STRIKES:
                price = om.bs_price(S, k, T, R, SIG, right)
                quoted = price > 0.10
                rows.append(
                    {
                        "contractSymbol": occ(exp, right, k),
                        "strike": k,
                        "lastPrice": price,
                        "bid": price - 0.05 if quoted else 0.0,
                        "ask": price + 0.05 if quoted else 0.0,
                        "volume": 100.0,
                        "openInterest": 1000.0,
                        "impliedVolatility": 1e-5,  # what yfinance reports off-hours
                    }
                )
            return pd.DataFrame(rows)

        return _Chain(side("C"), side("P"), self.market_state)

    def history(self, **_):
        return pd.DataFrame({"Close": [self.close]})

    @property
    def calendar(self):
        return {"Earnings Date": [self.earnings]} if self.earnings else {}


class _Chain:
    def __init__(self, calls, puts, state):
        self.calls, self.puts = calls, puts
        self.underlying = {"regularMarketPrice": S, "marketState": state}


@pytest.fixture
def chain(monkeypatch):
    BSTicker.market_state = "REGULAR"
    BSTicker.close = S
    BSTicker.earnings = None
    monkeypatch.setattr(options.yf, "Ticker", BSTicker)
    monkeypatch.setattr(options, "risk_free_rate", lambda: R)
    monkeypatch.setattr(options, "dividend_yield", lambda _u: 0.0)
    return BSTicker


def _price(sym, *_, **__):
    if options.is_option_symbol(sym):
        return options.option_price(sym)
    return {"^VIX": 20.0}.get(sym, S)


def _data_service():
    ds = MagicMock()
    ds.get_latest_price.side_effect = _price
    ds.get_latest_prices_batch.side_effect = lambda syms: {s: _price(s) for s in syms}
    return ds


@pytest.fixture
def pm(sqlite_db, db_session, test_bot, chain):
    return PortfolioManager(test_bot, test_bot.name, _data_service(), BotRepository, execution_config=NO_COSTS)


def _portfolio(db_session, name="TestBot") -> dict:
    db_session.expire_all()
    return dict(db_session.query(BotModel).filter_by(name=name).one().portfolio)


def _set_portfolio(db_session, portfolio, name="TestBot"):
    bot = db_session.query(BotModel).filter_by(name=name).one()
    bot.portfolio = portfolio
    db_session.commit()


def _nearest_delta_strike(expiry, right, target):
    T = om.year_fraction(expiry, TODAY)
    quoted = [k for k in STRIKES if om.bs_price(S, k, T, R, SIG, right) > 0.10]
    return min(quoted, key=lambda k: abs(abs(om.delta(S, k, T, R, SIG, right)) - target))


# ------------------------------------------------------------------
# Selection
# ------------------------------------------------------------------


def test_select_contract_by_delta(sqlite_db, chain):
    chosen = options.select_contract("AAPL", "C", target_dte=500, delta=0.70)
    assert chosen == occ(E560, "C", _nearest_delta_strike(E560, "C", 0.70))


def test_iv_is_solved_not_taken_from_yfinance(sqlite_db, chain):
    view = options.load_chain("AAPL", 35)
    assert options.atm_iv(view) == pytest.approx(SIG, abs=1e-3)


def test_select_vertical_put_spread(sqlite_db, chain):
    pick = options.select_vertical("AAPL", "P", short_delta=0.30, width=10, target_dte=35)
    short_k = _nearest_delta_strike(E40, "P", 0.30)
    assert pick.expiry == E40 and pick.live
    assert pick.legs == ((occ(E40, "P", short_k), -1), (occ(E40, "P", short_k - 10), 1))


def test_select_iron_condor_with_its_own_call_delta(sqlite_db, chain):
    pick = options.select_iron_condor("AAPL", short_delta=0.16, width=10, target_dte=35, call_delta=0.10)
    kc = _nearest_delta_strike(E40, "C", 0.10)
    assert pick.legs[2:] == ((occ(E40, "C", kc), -1), (occ(E40, "C", kc + 10), 1))
    assert kc > _nearest_delta_strike(E40, "C", 0.16)


def test_select_iron_condor(sqlite_db, chain):
    pick = options.select_iron_condor("AAPL", short_delta=0.16, width=10, target_dte=35)
    kp, kc = _nearest_delta_strike(E40, "P", 0.16), _nearest_delta_strike(E40, "C", 0.16)
    assert pick.legs == (
        (occ(E40, "P", kp), -1),
        (occ(E40, "P", kp - 10), 1),
        (occ(E40, "C", kc), -1),
        (occ(E40, "C", kc + 10), 1),
    )


# ------------------------------------------------------------------
# Margin and execution
# ------------------------------------------------------------------


def test_margin_requirement():
    put_spread = {occ(E40, "P", 280): -300, occ(E40, "P", 270): 300}
    assert options.margin_requirement({"USD": 1, **put_spread}) == pytest.approx(3000)
    condor = {**put_spread, occ(E40, "C", 320): -300, occ(E40, "C", 335): 300}
    assert options.margin_requirement(condor) == pytest.approx(4500)  # the wider wing only
    assert options.margin_requirement({occ(E40, "C", 320): 100}) == 0
    assert options.margin_requirement({occ(E40, "C", 320): -100}) == math.inf


def test_open_bull_put_spread_sized_by_max_risk(pm, db_session):
    pick = options.select_vertical("AAPL", "P", 0.30, 10, 35)
    (short, _), (long_, _) = pick.legs
    credit = options.latest_quote(short).bid - options.latest_quote(long_).ask
    per_unit = (10 - credit) * 100
    units = pm.open_structure(pick, max_risk_usd=3000)

    assert units == int(3000 // per_unit) and units >= 1
    book = _portfolio(db_session)
    assert book[short] == -100 * units and book[long_] == 100 * units
    assert book["USD"] == pytest.approx(10000 + credit * 100 * units)
    assert options.margin_requirement(book) == pytest.approx(1000 * units)
    trades = db_session.query(Trade).all()
    assert sorted((t.symbol, t.isBuy) for t in trades) == sorted([(short, False), (long_, True)])


def test_open_structure_refused_off_hours(pm, db_session, chain):
    chain.market_state = "CLOSED"
    pick = options.select_vertical("AAPL", "P", 0.30, 10, 35)
    assert not pick.live
    assert pm.open_structure(pick, 3000) == 0
    assert _portfolio(db_session) == {"USD": 10000.0}


def test_naked_short_call_is_refused_and_nothing_is_written(pm, db_session):
    options.load_chain("AAPL", 35)  # store quotes
    with pytest.raises(ValueError, match="margin"):
        pm.trade_option_legs([(occ(E40, "C", 320), -100)])
    assert _portfolio(db_session) == {"USD": 10000.0}
    assert db_session.query(Trade).count() == 0


def test_spread_larger_than_cash_is_refused(pm, db_session):
    options.load_chain("AAPL", 35)
    with pytest.raises(ValueError, match="margin"):
        pm.trade_option_legs([(occ(E40, "P", 300), -2000), (occ(E40, "P", 290), 2000)])  # $20k at risk
    assert _portfolio(db_session) == {"USD": 10000.0}


def test_buy_cannot_spend_reserved_margin(pm, db_session):
    pm.open_structure(options.select_vertical("AAPL", "P", 0.30, 10, 35), max_risk_usd=3000)
    before = _portfolio(db_session)
    reserved = options.margin_requirement(before)
    pm.buy("QQQ")  # all spendable cash
    after = _portfolio(db_session)
    assert after["USD"] == pytest.approx(reserved)
    assert after["QQQ"] * S == pytest.approx(before["USD"] - reserved)


def test_short_legs_are_valued_as_liabilities(pm, db_session):
    pm.open_structure(options.select_vertical("AAPL", "P", 0.30, 10, 35), max_risk_usd=3000)
    db_session.expire_all()
    bot = db_session.query(BotModel).filter_by(name="TestBot").one()
    worth = calculate_portfolio_worth(bot, _data_service())
    # At mid the spread is worth what was received, less the half-spread paid on each leg.
    assert 9_900 < worth < 10_000
    assert worth == pytest.approx(pm.total_value())


def test_book_entry_value_pnl_and_greeks(pm, db_session):
    units = pm.open_structure(options.select_vertical("AAPL", "P", 0.30, 10, 35), max_risk_usd=3000)
    book = pm.option_book("AAPL")
    assert book.credit > 0
    assert book.pnl == pytest.approx(-0.10 * 100 * units)  # both legs crossed a 5-cent half-spread
    assert book.dte == 40
    assert book.greeks.delta > 0  # a bull put spread is long delta
    assert book.greeks.theta > 0  # and collects time decay
    assert book.max_loss == pytest.approx(1000 * units - book.credit)


def test_close_options_flattens_every_leg(pm, db_session):
    pm.open_structure(options.select_iron_condor("AAPL", 0.16, 10, 35), max_risk_usd=3000)
    assert len(options.option_legs(_portfolio(db_session))) == 4
    pm.close_options("AAPL")
    book = _portfolio(db_session)
    assert set(book) == {"USD"}
    assert book["USD"] < 10000  # paid the spread twice
    assert pm.option_book("AAPL").empty


def test_entry_value_tracks_partial_closes(pm, db_session):
    key = occ(E40, "P", 300)
    for is_buy, qty, price in [(False, 300, 5.0), (True, 100, 4.0)]:
        BotRepository.log_trade("TestBot", key, qty, price, is_buy)
    # Sold 3 at 5.00 (credit 1500), bought 1 back: 2/3 of the credit still open.
    assert options.entry_value("TestBot", key) == pytest.approx(-1000.0)


# ------------------------------------------------------------------
# Expiry
# ------------------------------------------------------------------


def test_expired_spread_settles_short_as_a_debit(pm, db_session, chain):
    past = TODAY - timedelta(days=1)
    chain.close = 275.0  # put spread 290/280 fully in the money
    _set_portfolio(db_session, {"USD": 10000.0, occ(past, "P", 290): -200, occ(past, "P", 280): 200})
    pm.roll_and_settle_options(roll_dte=7, target_dte=30)
    assert _portfolio(db_session) == {"USD": pytest.approx(10000 - 15 * 200 + 5 * 200)}


def test_auto_roll_never_touches_an_underlying_with_short_legs(pm, db_session):
    legs = {occ(NEAR, "P", 290): -100, occ(NEAR, "P", 280): 100}
    _set_portfolio(db_session, {"USD": 10000.0, **legs})
    pm.roll_and_settle_options(roll_dte=7, target_dte=30)
    assert _portfolio(db_session) == {"USD": 10000.0, **legs}


def test_roll_dte_none_disables_rolling(pm, db_session):
    _set_portfolio(db_session, {"USD": 10000.0, occ(NEAR, "C", 300): 100})
    pm.roll_and_settle_options(roll_dte=None, target_dte=30)
    assert _portfolio(db_session)[occ(NEAR, "C", 300)] == 100


def test_rebalance_refuses_books_with_short_legs(pm, db_session):
    _set_portfolio(db_session, {"USD": 10000.0, occ(E40, "P", 290): -100, occ(E40, "P", 280): 100})
    with pytest.raises(ValueError, match="short option legs"):
        pm.rebalance_portfolio({"QQQ": 1.0})


def test_initial_capital_applies_only_on_creation(sqlite_db, db_session):
    assert BotRepository.create_or_get_bot("Rich", initial_usd=100_000).portfolio == {"USD": 100_000.0}
    assert BotRepository.create_or_get_bot("Rich", initial_usd=5).portfolio == {"USD": 100_000.0}
    assert BotRepository.create_or_get_bot("Default").portfolio == {"USD": 10000}


# ------------------------------------------------------------------
# Rules (pure)
# ------------------------------------------------------------------


def test_trend_side():
    assert rules_mod.trend_side(110, 105, 100) == "bull"
    assert rules_mod.trend_side(90, 95, 100) == "bear"
    assert rules_mod.trend_side(100, 105, 95) is None


def test_premium_selling_gate():
    r = rules_mod.CreditRules(min_iv_hv=1.1, max_vix=35, max_adx=25)
    assert rules_mod.premium_selling_ok(1.2, 20, 18, r)
    assert not rules_mod.premium_selling_ok(1.0, 20, 18, r)  # IV not rich
    assert not rules_mod.premium_selling_ok(1.2, 40, 18, r)  # panic
    assert not rules_mod.premium_selling_ok(1.2, 20, 30, r)  # trending
    assert not rules_mod.premium_selling_ok(None, 20, 18, r)


def test_credit_exits():
    r = rules_mod.CreditRules()
    assert rules_mod.credit_exit_reason(200, 100, 30, r).startswith("take profit")
    assert rules_mod.credit_exit_reason(200, -400, 30, r).startswith("stop loss")
    assert "DTE" in rules_mod.credit_exit_reason(200, 0, 21, r)
    assert rules_mod.credit_exit_reason(200, 50, 30, r) is None


def test_earnings_clear():
    assert rules_mod.earnings_clear(TODAY + timedelta(days=60), E40, TODAY)
    assert not rules_mod.earnings_clear(TODAY + timedelta(days=10), E40, TODAY)
    assert rules_mod.earnings_clear(None, E40, TODAY)


def test_leap_rules():
    r = rules_mod.LeapRules()
    assert rules_mod.leap_signal(110, 100, r) == 1
    assert rules_mod.leap_signal(98, 100, r) == 0  # inside the 3% buffer
    assert rules_mod.leap_signal(96, 100, r) == -1
    # $100k at 1x through 0.70-delta calls on a $336 stock: 4 contracts (~$94k of delta).
    assert rules_mod.leap_contracts(100_000, 336, 0.70, 1.0) == 4


def test_leap_trim_contracts():
    r = rules_mod.LeapRules(max_leverage=1.5, leverage=1.0)
    assert rules_mod.leap_trim_contracts(140_000, 100_000, 20_000, r) == 0  # 1.4x: inside the band
    assert rules_mod.leap_trim_contracts(190_000, 100_000, 20_000, r) == 5  # 90k over 1x -> 4.5 -> 5
    assert rules_mod.leap_trim_contracts(190_000, 100_000, 20_000, rules_mod.LeapRules()) == 0  # no trim


def test_credit_side_bull_only():
    bull_only = rules_mod.CreditRules(sides="bull")
    assert rules_mod.credit_side(100, 105, 95, bull_only) == "bull"  # mixed tape: still sells puts
    assert rules_mod.credit_side(90, 95, 100, bull_only) is None  # never fights a downtrend
    assert rules_mod.credit_side(90, 95, 100, rules_mod.CreditRules()) == "bear"


def test_catalyst_rules():
    r = rules_mod.CatalystRules()
    monday = pd.Timestamp("2026-10-05").date()
    assert rules_mod.catalyst_entry_ok(monday, monday + timedelta(days=21), r)  # 15 trading days
    assert not rules_mod.catalyst_entry_ok(monday, monday + timedelta(days=7), r)
    assert not rules_mod.catalyst_entry_ok(monday, None, r)
    expiry = monday + timedelta(days=40)
    assert rules_mod.catalyst_exit_reason(monday, monday + timedelta(days=1), expiry, 0.1, r)  # day before
    assert rules_mod.catalyst_exit_reason(monday, monday + timedelta(days=90), expiry, 0.1, r)  # missed it
    assert rules_mod.catalyst_exit_reason(monday, monday + timedelta(days=14), expiry, 0.6, r)  # +60%
    assert rules_mod.catalyst_exit_reason(monday, monday + timedelta(days=14), expiry, 0.1, r) is None


# ------------------------------------------------------------------
# The bots, end to end on the fake chain
# ------------------------------------------------------------------


def _series(wiggle: float, slope: float = 0.002, n: int = 300, adx: float = 15.0) -> pd.DataFrame:
    """
    Deterministic daily closes ending near S: an uptrend of `slope` per day in
    log terms, with alternating +/-wiggle so realized vol is ~2 x wiggle x sqrt(252).
    """
    i = np.arange(n)
    close = S * np.exp(slope * (i - (n - 1)) + wiggle * (-1.0) ** i)
    return pd.DataFrame({"close": close, "trend_adx": adx})


RICH_IV = 0.004  # HV ~13% against IV 25%: premium is rich
FAIR_IV = 0.008  # HV ~25%
CHEAP_IV = 0.010  # HV ~32%: options cheap
WILD = 0.015  # HV ~48%


def _make_bot(cls, mocker, data):
    mocker.patch("tradingbot.utils.botclass.init_db")
    bot = cls()
    ds = _data_service()
    bot._data_service = ds
    bot._portfolio_manager.data_service = ds
    bot._portfolio_manager.execution_config = NO_COSTS
    mocker.patch.object(bot, "getYFDataWithTA", return_value=data)
    return bot


def test_credit_spread_bot_opens_then_takes_profit(sqlite_db, db_session, chain, mocker):
    bot = _make_bot(OptionCreditSpreadBot, mocker, _series(RICH_IV))
    assert bot.dbBot.portfolio == {"USD": 100_000.0}
    assert bot.makeOneIteration() == 1
    legs = options.option_legs(_portfolio(db_session, "option_CreditSpreadBot"))
    assert sorted(legs.values())[0] < 0 < sorted(legs.values())[-1]  # one short, one long
    assert all(options.parse_occ(k).right == "P" for k in legs)  # uptrend -> bull put spread

    # Holding and nothing changed: hold.
    assert bot.makeOneIteration() == 0
    # Simulate a big profit: the recorded credit is now far above the mark.
    mocker.patch.object(options.OptionBook, "pnl", new_callable=mocker.PropertyMock, return_value=1e6)
    assert bot.makeOneIteration() == -1
    assert set(_portfolio(db_session, "option_CreditSpreadBot")) == {"USD"}


def test_credit_spread_bot_waits_when_iv_is_not_rich(sqlite_db, db_session, chain, mocker):
    bot = _make_bot(OptionCreditSpreadBot, mocker, _series(WILD))
    bot.makeOneIteration()
    assert _portfolio(db_session, "option_CreditSpreadBot") == {"USD": 100_000.0}


def test_credit_spread_bot_skips_earnings_inside_expiry(sqlite_db, db_session, chain, mocker):
    chain.earnings = TODAY + timedelta(days=20)
    bot = _make_bot(OptionCreditSpreadBot, mocker, _series(RICH_IV))
    assert bot.makeOneIteration() == 0
    assert _portfolio(db_session, "option_CreditSpreadBot") == {"USD": 100_000.0}


def test_iron_condor_bot_opens_four_legs_within_risk(sqlite_db, db_session, chain, mocker):
    bot = _make_bot(OptionIronCondorBot, mocker, _series(RICH_IV))
    assert bot.makeOneIteration() == 1
    book = _portfolio(db_session, "option_IronCondorBot")
    assert len(options.option_legs(book)) == 4
    assert bot.option_book("AAPL").max_loss <= 0.25 * 100_000


def test_iron_condor_bot_stands_aside_in_a_trend(sqlite_db, db_session, chain, mocker):
    bot = _make_bot(OptionIronCondorBot, mocker, _series(RICH_IV, adx=40.0))
    assert bot.makeOneIteration() == 0


def test_leap_bot_buys_delta_sized_leap(sqlite_db, db_session, chain, mocker):
    # Calm uptrend: IV 25% against HV ~13% is expensive time, so no buy...
    bot = _make_bot(OptionLeapCallBot, mocker, _series(RICH_IV))
    assert bot.makeOneIteration() == 0
    # ...but with realized vol matching implied, it buys.
    bot.getYFDataWithTA.return_value = _series(FAIR_IV)
    assert bot.makeOneIteration() == 1
    legs = options.option_legs(_portfolio(db_session, "option_LeapCallBot"))
    ((key, qty),) = legs.items()
    c = options.parse_occ(key)
    assert c.expiry == E560 and c.right == "C"
    target = OptionLeapCallBot.RULES.delta
    assert qty == 100 * rules_mod.leap_contracts(100_000, S, target, 1.0)
    assert abs(om.delta(S, c.strike, om.year_fraction(E560, TODAY), R, SIG, "C") - target) < 0.03


def test_leap_bot_trims_exposure_back_to_one_times_book(sqlite_db, db_session, chain, mocker):
    bot = _make_bot(OptionLeapCallBot, mocker, _series(FAIR_IV))
    assert bot.makeOneIteration() == 1
    ((key, qty),) = options.option_legs(_portfolio(db_session, "option_LeapCallBot")).items()
    # Gains have tripled the position (as a rally plus rising delta would).
    _set_portfolio(db_session, {**_portfolio(db_session, "option_LeapCallBot"), key: 3 * qty}, "option_LeapCallBot")

    def leverage():
        book = bot.option_book("AAPL")
        return book.greeks.delta * book.spot / bot.portfolio_value()

    assert leverage() > 1.5
    assert bot.makeOneIteration() == -1
    assert 0.8 < leverage() <= 1.0 + 1e-9  # trimmed to 1x, rounding contracts up
    assert bot.makeOneIteration() == 0  # within the band now: nothing more to do


def test_catalyst_bot_enters_in_window_and_exits_before_earnings(sqlite_db, db_session, chain, mocker):
    data = _series(CHEAP_IV)
    chain.earnings = TODAY + timedelta(days=21)
    mocker.patch.object(rules_mod, "business_days", return_value=15)
    bot = _make_bot(OptionCatalystCallBot, mocker, data)
    assert bot.makeOneIteration() == 1
    (key,) = options.option_legs(_portfolio(db_session, "option_CatalystCallBot"))
    assert options.parse_occ(key).expiry >= chain.earnings + timedelta(days=14)

    mocker.patch.object(rules_mod, "business_days", return_value=1)  # the day before earnings
    assert bot.makeOneIteration() == -1
    assert set(_portfolio(db_session, "option_CatalystCallBot")) == {"USD"}


# ------------------------------------------------------------------
# Round 2: shares inside structures, physical settlement, hedging
# ------------------------------------------------------------------


def test_margin_counts_shares_beside_options():
    call = occ(E40, "C", 320)
    assert options.margin_requirement({"USD": 0, "AAPL": 100, call: -100}) == 0  # covered call
    assert options.margin_requirement({"USD": 0, "AAPL": 50, call: -100}) == math.inf  # half covered
    assert options.margin_requirement({"USD": 0, occ(E40, "P", 280): -100}) == pytest.approx(28_000)
    assert options.margin_requirement({"USD": 0, "AAPL": -10}) == math.inf  # naked short stock
    assert options.margin_requirement({"USD": 0, "QQQ": 10}) == 0  # plain stock bots are untouched
    hedged = {occ(E40, "C", 300): 100, occ(E40, "P", 300): 100, "AAPL": -30}
    assert options.margin_requirement(hedged) == pytest.approx(30 * 300)


def test_buy_write_opens_shares_and_call_together(pm, db_session):
    _set_portfolio(db_session, {"USD": 100_000.0})
    options.load_chain("AAPL", 35)
    call = occ(E40, "C", 320)
    pm.trade_option_legs([("AAPL", 100), (call, -100)])
    book = _portfolio(db_session)
    assert book["AAPL"] == 100 and book[call] == -100
    assert options.margin_requirement(book) == 0
    trades = {t.symbol: t for t in db_session.query(Trade).all()}
    assert trades["AAPL"].isBuy and trades["AAPL"].price == pytest.approx(S)


def test_stock_legs_need_options_beside_them(pm, db_session):
    with pytest.raises(ValueError, match="beside options"):
        pm.trade_option_legs([("AAPL", -10)])
    assert _portfolio(db_session) == {"USD": 10000.0}


def test_sell_refuses_to_uncover_a_short_call(pm, db_session):
    call = occ(E40, "C", 320)
    options.load_chain("AAPL", 35)
    _set_portfolio(db_session, {"USD": 1000.0, "AAPL": 150.0, call: -100})
    assert pm.sell("AAPL", quantity_usd=100 * S) == 0.0  # would leave 50 shares under 100 calls
    assert _portfolio(db_session)["AAPL"] == 150
    assert pm.sell("AAPL", quantity_usd=50 * S) > 0  # the spare 50 are free to go
    assert _portfolio(db_session)["AAPL"] == pytest.approx(100)


def test_physical_settlement_assigns_and_delivers(pm, db_session, chain):
    past = TODAY - timedelta(days=1)
    chain.close = 280.0
    put, call = occ(past, "P", 290), occ(past, "C", 270)
    _set_portfolio(db_session, {"USD": 60_000.0, put: -200})
    pm.roll_and_settle_options(roll_dte=None, target_dte=30, settlement="physical")
    assert _portfolio(db_session) == {"USD": pytest.approx(60_000 - 200 * 290), "AAPL": 200}
    stock_trade = db_session.query(Trade).filter_by(symbol="AAPL").one()
    assert stock_trade.isBuy and stock_trade.price == 290  # assigned at the strike

    _set_portfolio(db_session, {"USD": 0.0, "AAPL": 100, call: -100})  # covered call finishes ITM
    pm.roll_and_settle_options(roll_dte=None, target_dte=30, settlement="physical")
    assert _portfolio(db_session) == {"USD": pytest.approx(100 * 270)}  # called away


def test_physical_settlement_never_creates_short_stock(pm, db_session, chain):
    past = TODAY - timedelta(days=1)
    chain.close = 280.0
    put = occ(past, "P", 300)
    _set_portfolio(db_session, {"USD": 1000.0, "AAPL": 50, put: 100})  # long put, only 50 shares to deliver
    pm.roll_and_settle_options(roll_dte=None, target_dte=30, settlement="physical")
    # 50 shares put to the market at 300, the other 50 units cash-settled at 20.
    assert _portfolio(db_session) == {"USD": pytest.approx(1000 + 50 * 300 + 50 * 20)}


def test_delta_hedge_flattens_a_straddle(pm, db_session):
    _set_portfolio(db_session, {"USD": 50_000.0})
    view = options.load_chain("AAPL", 35)
    pm.open_structure(options.select_straddle(view), max_risk_usd=5_000)
    before = pm.option_book("AAPL")
    assert abs(before.net_delta) > 1
    shares = pm.delta_hedge("AAPL", band_usd=100)
    after = pm.option_book("AAPL")
    assert shares == -round(before.net_delta)
    assert abs(after.net_delta) <= 0.5
    pm.close_options("AAPL", include_stock=True)
    assert set(_portfolio(db_session)) == {"USD"}


def test_new_builders(sqlite_db, chain):
    view = options.load_chain("AAPL", 35)
    fly = options.select_iron_butterfly(view, 20)
    assert fly.legs == (
        (occ(E40, "P", 280), 1),
        (occ(E40, "P", 300), -1),
        (occ(E40, "C", 300), -1),
        (occ(E40, "C", 320), 1),
    )
    assert options.select_straddle(view).legs == ((occ(E40, "C", 300), 1), (occ(E40, "P", 300), 1))
    collar = options.select_collar(view, 0.25, 0.25)
    assert collar.legs[0] == ("AAPL", 1)
    assert collar.legs[1] == (occ(E40, "P", _nearest_delta_strike(E40, "P", 0.25)), 1)
    assert collar.legs[2] == (occ(E40, "C", _nearest_delta_strike(E40, "C", 0.25)), -1)
    capped = options.select_short_leg(view, "C", 0.30, min_strike=330)
    assert options.parse_occ(capped.legs[0][0]).strike >= 330

    back = options.load_chain("AAPL", 70)
    cal = options.select_calendar(view, back)
    assert cal.legs == ((occ(E40, "C", 300), -1), (occ(E75, "C", 300), 1))
    leap = options.load_chain("AAPL", 500)
    diag = options.select_diagonal(leap, view, 0.80, 0.30, min_short_strike=310)
    (long_key, _), (short_key, _) = diag.legs
    assert options.parse_occ(long_key).expiry == E560 and options.parse_occ(short_key).expiry == E40
    assert options.parse_occ(short_key).strike >= 310
    with pytest.raises(ValueError):
        options.select_calendar(back, view)


def test_copier_keeps_structure_stock_away_from_the_broker():
    copier = LiveTradeCopier(broker=MagicMock(), bot_weights={"option_X": 1.0}, dry_run=True)
    bot = MagicMock(spec=BotModel)
    call = occ(E40, "C", 320)
    bot.portfolio = {"USD": 1000.0, "AAPL": 100.0, call: -100.0, "QQQ": 2.0}
    copier.bot_repo = MagicMock()
    copier.bot_repo.create_or_get_bot.return_value = bot
    copier.data_service = MagicMock()
    copier.data_service.get_latest_prices_batch.return_value = {"AAPL": 300.0, call: 5.0, "QQQ": 500.0}
    weights = copier._calculate_target_weights()
    total = 1000 + 30_000 - 500 + 1000
    assert weights == {"QQQ": pytest.approx(1000 / total)}


# ------------------------------------------------------------------
# Round 2 rules (pure)
# ------------------------------------------------------------------


def test_mispricing_rules():
    r = rules_mod.MispricingRules()
    assert rules_mod.mispricing_side(0.35, 0.25, r) == "rich"
    assert rules_mod.mispricing_side(0.20, 0.25, r) == "cheap"
    assert rules_mod.mispricing_side(0.28, 0.25, r) is None
    assert rules_mod.mispricing_side(None, 0.25, r) is None
    assert rules_mod.butterfly_width(300, 0.25, 0.25, r) == pytest.approx(37.5)
    # Rich butterfly: credit 500.
    assert rules_mod.mispricing_exit_reason("rich", 0.05, 130, -500, 30, 3, r).startswith("take profit")
    assert rules_mod.mispricing_exit_reason("rich", 0.05, -600, -500, 30, 3, r).startswith("stop")
    assert rules_mod.mispricing_exit_reason("rich", 0.0, 0, -500, 30, 3, r).startswith("gap closed")
    assert rules_mod.mispricing_exit_reason("rich", 0.05, 0, -500, 8, 3, r).endswith("10")
    assert rules_mod.mispricing_exit_reason("rich", 0.05, 0, -500, 30, 3, r) is None
    # Cheap straddle: debit 1000.
    assert rules_mod.mispricing_exit_reason("cheap", -0.05, 400, 1000, 30, 3, r).startswith("take profit")
    assert rules_mod.mispricing_exit_reason("cheap", -0.05, 0, 1000, 30, 15, r).startswith("held")
    assert rules_mod.mispricing_exit_reason("cheap", 0.0, 0, 1000, 30, 3, r).startswith("gap closed")
    assert rules_mod.mispricing_exit_reason("cheap", -0.05, 0, 1000, 30, 3, r) is None


def test_explain_gap_walks_the_reasons():
    r = rules_mod.MispricingRules()
    assert not rules_mod.explain_gap(0.27, 0.25, r, earnings_inside=False).tradeable
    e = rules_mod.explain_gap(0.40, 0.25, r, earnings_inside=True, implied_move=0.05, hist_move=0.04)
    assert not e.tradeable and "earnings" in e.reason and "5.0%" in e.summary()
    e = rules_mod.explain_gap(0.40, 0.25, r, earnings_inside=False, relative_iv=1.4, news_event="DOJ ruling")
    assert not e.tradeable and "DOJ ruling" in e.reason  # AAPL-specific and explained by news
    e = rules_mod.explain_gap(0.40, 0.25, r, earnings_inside=False, relative_iv=1.0, news_event="DOJ ruling")
    assert e.tradeable and e.side == "rich"  # market-wide: the news is not why
    assert not rules_mod.explain_gap(0.60, 0.25, r, earnings_inside=False, vix=45).tradeable
    e = rules_mod.explain_gap(0.15, 0.25, r, earnings_inside=False, relative_iv=0.7, news_event="tariff")
    assert e.tradeable and e.side == "cheap"  # news never blocks buying cheap vol


def test_classify_news_llm_then_keywords():
    heads = ["Apple faces DOJ antitrust ruling next week", "iPhone reviews are in"]
    until = TODAY + timedelta(days=30)
    assert rules_mod.classify_news(
        heads, "AAPL", until, lambda *_: '{"pending_event": true, "event": "DOJ ruling"}'
    ) == ("DOJ ruling")
    assert rules_mod.classify_news(heads, "AAPL", until, lambda *_: 'x {"pending_event": false, "event": ""}') is None
    assert rules_mod.classify_news(heads, "AAPL", until, lambda *_: "sorry, I cannot") == heads[0]  # keyword fallback

    def broken(*_):
        raise ValueError("no API key")

    assert rules_mod.classify_news(["Apple ships new watch"], "AAPL", until, broken) is None
    assert rules_mod.classify_news([], "AAPL", until, broken) is None


def test_wheel_pmcc_collar_calendar_rules():
    w = rules_mod.WheelRules()
    assert rules_mod.wheel_put_ok(None, 100, 90, False, w)  # defaults gate nothing
    assert not rules_mod.wheel_put_ok(1.0, 100, 90, True, rules_mod.WheelRules(min_iv_hv=1.1))
    assert not rules_mod.wheel_put_ok(1.2, 80, 90, True, rules_mod.WheelRules(trend_filter=True))
    assert rules_mod.wheel_call_floor(287.5, w) == 287.5
    assert rules_mod.wheel_call_floor(287.5, rules_mod.WheelRules(call_floor=None)) is None
    assert rules_mod.short_premium_exit_reason(200, 100, 30, 0.5, None).startswith("take profit")
    assert rules_mod.short_premium_exit_reason(200, 50, 3, 0.5, None) is None  # held to expiry

    p = rules_mod.PMCCRules()
    assert rules_mod.pmcc_min_short_strike(240, 70, 300) == pytest.approx(250)  # 10 of extrinsic paid
    assert rules_mod.pmcc_short_exit_reason(200, 0, 30, 0.65, p).startswith("short call delta")
    assert rules_mod.pmcc_short_exit_reason(200, 120, 30, 0.2, p).startswith("take profit")
    assert "DTE" in rules_mod.pmcc_short_exit_reason(200, 0, 5, 0.2, p)
    assert rules_mod.pmcc_leap_rules(p).delta == 0.80

    c = rules_mod.CollarRules
    assert rules_mod.collar_wanted(100, 110, None, c())
    assert rules_mod.collar_wanted(100, 110, None, c(mode="below_sma200"))
    assert not rules_mod.collar_wanted(120, 110, None, c(mode="below_sma200"))
    assert rules_mod.collar_wanted(100, 110, 0.9, c(mode="iv_cheap"))
    assert not rules_mod.collar_wanted(100, 110, 1.3, c(mode="iv_cheap"))

    k = rules_mod.CalendarRules()
    monday = pd.Timestamp("2026-10-05").date()
    assert rules_mod.calendar_entry_window(monday, monday + timedelta(days=7), k)  # 5 trading days
    assert not rules_mod.calendar_entry_window(monday, monday + timedelta(days=21), k)
    assert rules_mod.calendar_signal_ok(0.40, 0.30, 0.05, 0.04, k)
    assert not rules_mod.calendar_signal_ok(0.32, 0.30, 0.05, 0.04, k)  # front not inflated
    assert not rules_mod.calendar_signal_ok(0.40, 0.30, 0.03, 0.04, k)  # crush not rich
    report = monday + timedelta(days=2)
    assert rules_mod.calendar_exit_reason(monday + timedelta(days=3), monday, report, 0.0, 10, k).startswith("earn")
    assert rules_mod.calendar_exit_reason(report, monday, report, 0.0, 10, k) is None  # reports after the close
    assert rules_mod.calendar_exit_reason(monday, monday, None, -0.6, 10, k).startswith("stop")


# ------------------------------------------------------------------
# Round 2 bots, end to end on the fake chain
# ------------------------------------------------------------------


def _dated(df: pd.DataFrame) -> pd.DataFrame:
    """_series with a business-day timestamp column ending today (what getYFData returns)."""
    out = df.copy()
    out["timestamp"] = pd.bdate_range(end=pd.Timestamp(TODAY), periods=len(df) + 5)[-len(df) :]
    return out


def _make_vol_bot(cls, mocker, data):
    bot = _make_bot(cls, mocker, data)
    mocker.patch.object(bot, "getYFData", return_value=_dated(data))
    mocker.patch.object(options, "recent_news", return_value=[])
    return bot


def test_mispricing_bot_sells_rich_vol_with_a_butterfly(sqlite_db, db_session, chain, mocker):
    bot = _make_vol_bot(OptionMispricingBot, mocker, _series(RICH_IV))  # IV 25% vs HV ~13%
    assert bot.makeOneIteration() == 1
    legs = options.option_legs(_portfolio(db_session, "option_MispricingBot"))
    assert len(legs) == 4 and sum(legs.values()) == 0  # two shorts, two wings, same size
    strikes = sorted({options.parse_occ(k).strike for k, q in legs.items() if q < 0})
    assert strikes == [300.0]  # short the at-the-money straddle
    assert bot.option_book("AAPL").max_loss <= 0.20 * 100_000
    assert bot.makeOneIteration() == 0  # holding


def test_mispricing_bot_buys_cheap_vol_and_hedges_it(sqlite_db, db_session, chain, mocker):
    bot = _make_vol_bot(OptionMispricingBot, mocker, _series(WILD))  # IV 25% vs HV ~48%
    assert bot.makeOneIteration() == 1
    book = bot.option_book("AAPL")
    assert {p.contract.right for p in book.positions} == {"C", "P"} and all(p.qty > 0 for p in book.positions)
    assert book.shares != 0 and abs(book.net_delta) <= 0.5  # delta-hedged at the open
    assert bot.makeOneIteration() == 0  # holds, hedge unchanged


def test_mispricing_bot_leaves_fairly_priced_options_alone(sqlite_db, db_session, chain, mocker):
    bot = _make_vol_bot(OptionMispricingBot, mocker, _series(FAIR_IV))
    assert bot.makeOneIteration() == 0
    assert _portfolio(db_session, "option_MispricingBot") == {"USD": 100_000.0}


def test_mispricing_bot_respects_a_pending_event(sqlite_db, db_session, chain, mocker):
    bot = _make_vol_bot(OptionMispricingBot, mocker, _series(RICH_IV))
    options.recent_news.return_value = ["Court to rule on Apple App Store antitrust case Friday"]
    mocker.patch.object(bot, "_relative_iv", return_value=1.5)  # AAPL-specific richness
    mocker.patch.object(bot, "run_ai_simple", side_effect=ValueError("no key"))  # -> keyword fallback
    assert bot.makeOneIteration() == 0
    assert _portfolio(db_session, "option_MispricingBot") == {"USD": 100_000.0}


def test_wheel_bot_sells_puts_then_covered_calls(sqlite_db, db_session, chain, mocker):
    bot = _make_bot(OptionWheelBot, mocker, _series(RICH_IV))
    assert bot.makeOneIteration() == 1
    legs = options.option_legs(_portfolio(db_session, "option_WheelBot"))
    ((put, qty),) = legs.items()
    strike = options.parse_occ(put).strike
    assert options.parse_occ(put).right == "P" and qty < 0
    assert -qty * strike <= 100_000  # cash-secured

    # Assigned: 300 shares at 290, recorded as the broker would.
    _set_portfolio(db_session, {"USD": 13_000.0, "AAPL": 300.0}, "option_WheelBot")
    BotRepository.log_trade("option_WheelBot", "AAPL", 300, 290.0, True)
    bot.dbBot = BotRepository.create_or_get_bot("option_WheelBot")  # run() refreshes it after settlement
    assert bot.makeOneIteration() == 1
    legs = options.option_legs(_portfolio(db_session, "option_WheelBot"))
    ((call, qty),) = legs.items()
    assert options.parse_occ(call).right == "C" and qty == -300
    assert options.parse_occ(call).strike >= 290  # never below the cost basis


def test_pmcc_bot_buys_leap_then_sells_calls_against_it(sqlite_db, db_session, chain, mocker):
    bot = _make_bot(OptionPMCCBot, mocker, _series(FAIR_IV))
    assert bot.makeOneIteration() == 1
    ((leap, n),) = options.option_legs(_portfolio(db_session, "option_PMCCBot")).items()
    assert options.parse_occ(leap).expiry == E560 and n > 0
    assert bot.makeOneIteration() == 1
    legs = options.option_legs(_portfolio(db_session, "option_PMCCBot"))
    shorts = {k: q for k, q in legs.items() if q < 0}
    ((short, q),) = shorts.items()
    assert q == -n and options.parse_occ(short).expiry == E40
    lc = options.parse_occ(leap)
    assert options.parse_occ(short).strike >= lc.strike
    assert bot.makeOneIteration() == 0  # hold both
    mocker.patch("tradingbot.option_pmccbot.pmcc_short_exit_reason", return_value="take profit")
    assert bot.makeOneIteration() == -1
    assert list(options.option_legs(_portfolio(db_session, "option_PMCCBot"))) == [leap]


def test_collar_bot_buys_shares_and_collars_them(sqlite_db, db_session, chain, mocker):
    bot = _make_bot(OptionCollarBot, mocker, _series(FAIR_IV))
    assert bot.makeOneIteration() == 1
    book = _portfolio(db_session, "option_CollarBot")
    shares = book["AAPL"]
    assert shares == pytest.approx(0.97 * 100_000 / S, rel=0.01)
    legs = options.option_legs(book)
    n = int(shares // 100)
    assert sorted(legs.values()) == [-100 * n, 100 * n]
    assert options.margin_requirement(book) == 0
    assert bot.makeOneIteration() == 0


def test_earnings_calendar_bot(sqlite_db, db_session, chain, mocker):
    chain.earnings = E40 - timedelta(days=5)
    mocker.patch.object(rules_mod, "business_days", return_value=5)
    bot = _make_vol_bot(OptionEarningsCalendarBot, mocker, _series(FAIR_IV))
    assert bot.makeOneIteration() == 0  # flat vol: the front holds no event premium, no trade
    mocker.patch.object(
        OptionEarningsCalendarBot, "RULES", rules_mod.CalendarRules(min_term_ratio=0.9, min_implied_vs_hist=0.0)
    )
    mocker.patch.object(options, "earnings_history", return_value=[TODAY - timedelta(days=d) for d in (100, 190)])
    assert bot.makeOneIteration() == 1
    legs = options.option_legs(_portfolio(db_session, "option_EarningsCalendarBot"))
    front, back = occ(E40, "C", 300), occ(E75, "C", 300)
    assert set(legs) == {front, back} and legs[front] == -legs[back] < 0  # short front, long back
    assert bot.option_book("AAPL").entry_value <= 0.05 * 100_000
    mocker.patch.object(options.OptionBook, "pnl_pct", new_callable=mocker.PropertyMock, return_value=0.3)
    assert bot.makeOneIteration() == -1


# ------------------------------------------------------------------
# Index vol: SPY condors when implied beats forecast vol
# ------------------------------------------------------------------


def test_index_vol_rules():
    r = rules_mod.IndexVolRules(min_gap=0.03)
    assert rules_mod.index_vol_entry_ok(0.20, 0.16, 18.0, r)
    assert not rules_mod.index_vol_entry_ok(0.18, 0.16, 18.0, r)  # 2 pts: not enough premium
    assert not rules_mod.index_vol_entry_ok(0.50, 0.30, 45.0, r)  # panic tape
    assert not rules_mod.index_vol_entry_ok(None, 0.16, 18.0, r)
    assert rules_mod.index_vol_entry_ok(0.10, 0.16, 18.0, rules_mod.IndexVolRules(min_gap=None))
    assert rules_mod.index_vol_exit_reason(100, 50, 30, r).startswith("take profit")
    assert rules_mod.index_vol_exit_reason(100, -200, 30, r).startswith("stop")
    assert rules_mod.index_vol_exit_reason(100, 0, 21, r) == "21 DTE <= 21"
    assert rules_mod.index_vol_exit_reason(100, 10, 30, r) is None


def _make_index_bot(mocker, data):
    # The fake chain lists AAPL contracts; the rules do not care which index it is.
    mocker.patch("tradingbot.option_indexvolbot.UNDERLYING", "AAPL")
    return _make_vol_bot(OptionIndexVolBot, mocker, data)


def test_index_vol_bot_sells_a_wide_condor_when_iv_beats_the_forecast(sqlite_db, db_session, chain, mocker):
    bot = _make_index_bot(mocker, _series(RICH_IV))  # IV 25% vs HAR ~13%
    assert bot.makeOneIteration() == 1
    rules = OptionIndexVolBot.RULES
    legs = options.option_legs(_portfolio(db_session, "option_IndexVolBot"))
    assert len(legs) == 4 and sum(legs.values()) == 0
    shorts = {options.parse_occ(k).right: options.parse_occ(k).strike for k, q in legs.items() if q < 0}
    assert shorts == {r: _nearest_delta_strike(E40, r, d) for r, d in (("P", rules.put_delta), ("C", rules.call_delta))}
    # Wings reach toward width_pct of spot, stopping at the last strike with a quote.
    wings = sorted(options.parse_occ(k).strike for k, q in legs.items() if q > 0)
    assert shorts["P"] - rules.width_pct * S <= wings[0] < shorts["P"]
    assert shorts["C"] < wings[1] <= shorts["C"] + rules.width_pct * S
    assert bot.option_book("AAPL").max_loss <= rules.max_risk_pct * 100_000
    assert bot.makeOneIteration() == 0  # holding
    mocker.patch.object(options.OptionBook, "pnl", new_callable=mocker.PropertyMock, return_value=1e6)
    assert bot.makeOneIteration() == -1
    assert set(_portfolio(db_session, "option_IndexVolBot")) == {"USD"}


def test_index_vol_bot_waits_when_iv_is_below_the_forecast(sqlite_db, db_session, chain, mocker):
    bot = _make_index_bot(mocker, _series(WILD))  # IV 25% vs HAR ~48%
    assert bot.makeOneIteration() == 0
    assert _portfolio(db_session, "option_IndexVolBot") == {"USD": 100_000.0}


def test_index_vol_bot_does_not_open_off_hours(sqlite_db, db_session, chain, mocker):
    chain.market_state = "CLOSED"
    bot = _make_index_bot(mocker, _series(RICH_IV))
    assert bot.makeOneIteration() == 0
    assert _portfolio(db_session, "option_IndexVolBot") == {"USD": 100_000.0}
