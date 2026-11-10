"""
Tests for option_FOMCVolBot and utils/fomc_vol_rules.

Pure in-memory synthetic tests: no database connection or external services required.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import pytest

from tradingbot.option_fomcvolbot import OptionFOMCVolBot, RULES
from tradingbot.utils import macro_calendar, market_calendar, options
from tradingbot.utils.fomc_vol_rules import (
    DEFAULT_ENTRY_WINDOW_MINUTES,
    FOMCVolRules,
    decide_fomcvol,
    in_entry_window,
    is_fomc_entry_session,
    is_fomc_exit_session,
    next_fomc_date,
    select_fomc_structure,
)
from tradingbot.utils.option_decide import Close, Hedge, Holdings, Market, Open

NEW_YORK = ZoneInfo("America/New_York")


# ------------------------------------------------------------------
# Synthetic In-Memory Mock Classes
# ------------------------------------------------------------------


class SyntheticMarket(Market):
    """In-memory mock Market implementation for testing pure decide functions."""

    def __init__(
        self,
        today: date,
        now: datetime,
        chain_view: options.ChainView | None = None,
        closes_series: pd.Series | None = None,
    ):
        super().__init__(today, now)
        self._chain_view = chain_view
        self._closes = closes_series if closes_series is not None else pd.Series([500.0], index=[today])

    def chain(self, underlying: str, target_dte: int) -> options.ChainView | None:
        return self._chain_view

    def vol_index(self, symbol: str) -> float | None:
        return 18.0

    def closes(self, underlying: str) -> pd.Series:
        return self._closes

    def ohlc(self, symbols) -> dict[str, pd.DataFrame]:
        return {}


class SyntheticHoldings(Holdings):
    """In-memory mock Holdings implementation."""

    def __init__(
        self,
        underlyings_held: set[str] | None = None,
        equity_usd: float = 100_000.0,
        opened_date: date | None = None,
    ):
        self._underlyings = underlyings_held or set()
        self._equity = equity_usd
        self._opened_on = opened_date

    def underlyings(self) -> set[str]:
        return self._underlyings

    def book(self, underlying: str) -> options.OptionBook:
        return options.OptionBook(underlying, spot=500.0)

    def opened_on(self, underlying: str) -> date | None:
        return self._opened_on if underlying in self._underlyings else None

    def flows_since(self, underlying: str, since: date) -> float:
        return 0.0

    def equity(self) -> float:
        return self._equity


def _make_chain_view(
    underlying: str = "SPY",
    spot: float = 500.0,
    expiry: date | None = None,
    live: bool = True,
    today: date | None = None,
) -> options.ChainView:
    today = today or date(2026, 1, 27)
    expiry = expiry or date(2026, 1, 30)

    rows = []
    for strike in range(450, 555, 5):
        k = float(strike)
        for right in ("C", "P"):
            c_sym = f"{underlying}{expiry:%y%m%d}{right}{int(k * 1000):08d}"
            # Approximate synthetic pricing
            intrinsic = max(0.0, spot - k if right == "C" else k - spot)
            mid = max(intrinsic, 5.0)
            rows.append(
                {
                    "contract_symbol": c_sym,
                    "underlying": underlying,
                    "expiration": expiry,
                    "option_type": right,
                    "strike": k,
                    "bid": mid - 0.05,
                    "ask": mid + 0.05,
                    "last_price": mid,
                    "volume": 100,
                    "open_interest": 1000,
                    "implied_volatility": 0.20,
                    "delta": 0.50 if k == spot else (0.10 if (right == "C" and k > spot) else -0.10),
                }
            )
    frame = pd.DataFrame(rows)
    return options.ChainView(underlying, expiry, frame, spot, live, today)


# ------------------------------------------------------------------
# Unit Tests
# ------------------------------------------------------------------


def test_next_fomc_date():
    test_dates = (date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29))
    assert next_fomc_date(date(2026, 1, 1), test_dates) == date(2026, 1, 28)
    assert next_fomc_date(date(2026, 1, 28), test_dates) == date(2026, 1, 28)
    assert next_fomc_date(date(2026, 1, 29), test_dates) == date(2026, 3, 18)
    assert next_fomc_date(date(2026, 5, 1), test_dates) is None


def test_is_fomc_entry_session():
    # 2026-01-28 is a Wednesday FOMC statement date.
    # Tuesday 2026-01-27 is the entry session (1 session prior).
    fomc_day = date(2026, 1, 28)
    entry_day = date(2026, 1, 27)
    earlier_day = date(2026, 1, 26)
    weekend_day = date(2026, 1, 25)

    test_dates = (fomc_day,)
    assert is_fomc_entry_session(entry_day, test_dates) is True
    assert is_fomc_entry_session(earlier_day, test_dates) is False
    assert is_fomc_entry_session(fomc_day, test_dates) is False
    assert is_fomc_entry_session(weekend_day, test_dates) is False


def test_in_entry_window():
    # Regular close is 16:00 NY
    t_1530 = datetime(2026, 1, 27, 15, 30, tzinfo=NEW_YORK)  # 30 min to close -> True
    t_1100 = datetime(2026, 1, 27, 11, 0, tzinfo=NEW_YORK)   # 300 min to close -> False
    t_1605 = datetime(2026, 1, 27, 16, 5, tzinfo=NEW_YORK)   # past close -> False
    t_sat = datetime(2026, 1, 24, 15, 30, tzinfo=NEW_YORK)    # weekend -> False

    assert in_entry_window(t_1530, window_minutes=120) is True
    assert in_entry_window(t_1100, window_minutes=120) is False
    assert in_entry_window(t_1605, window_minutes=120) is False
    assert in_entry_window(t_sat, window_minutes=120) is False


def test_is_fomc_exit_session():
    fomc_day = date(2026, 1, 28)
    entry_day = date(2026, 1, 27)
    after_day = date(2026, 1, 29)
    test_dates = (fomc_day,)

    # On entry day -> False
    assert is_fomc_exit_session(entry_day, opened_on=entry_day, dates=test_dates) is False

    # On FOMC day before 14:00 NY -> False
    morning_now = datetime(2026, 1, 28, 10, 30, tzinfo=NEW_YORK)
    assert is_fomc_exit_session(fomc_day, opened_on=entry_day, now=morning_now, dates=test_dates) is False

    # On FOMC day at or after 14:00 NY -> True
    post_announcement = datetime(2026, 1, 28, 14, 5, tzinfo=NEW_YORK)
    assert is_fomc_exit_session(fomc_day, opened_on=entry_day, now=post_announcement, dates=test_dates) is True

    # After FOMC day -> True
    assert is_fomc_exit_session(after_day, opened_on=entry_day, dates=test_dates) is True


def test_select_fomc_structure():
    view = _make_chain_view("SPY", spot=500.0)

    # Straddle
    rules_straddle = FOMCVolRules(side="long", structure="straddle")
    pick_straddle = select_fomc_structure(view, rules_straddle)
    assert len(pick_straddle.legs) == 2
    assert all(lot == 1 for _, lot in pick_straddle.legs)

    # Short Iron Fly (5% wings)
    rules_fly = FOMCVolRules(side="short", structure="iron_fly", wing_width_pct=0.05)
    pick_fly = select_fomc_structure(view, rules_fly)
    assert len(pick_fly.legs) == 4
    lots = [lot for _, lot in pick_fly.legs]
    assert sorted(lots) == [-1, -1, 1, 1]

    # Strangle
    rules_strangle = FOMCVolRules(side="long", structure="strangle")
    pick_strangle = select_fomc_structure(view, rules_strangle)
    assert len(pick_strangle.legs) == 2


def test_decide_fomcvol_never_opens_off_hours():
    # 11:00 NY is outside the entry window (not last 120 minutes)
    entry_day = date(2026, 1, 27)
    morning_now = datetime(2026, 1, 27, 11, 0, tzinfo=NEW_YORK)
    view = _make_chain_view("SPY", spot=500.0, today=entry_day, live=True)

    market = SyntheticMarket(entry_day, morning_now, chain_view=view)
    holdings = SyntheticHoldings()
    rules = FOMCVolRules()

    actions = decide_fomcvol(market, holdings, rules, "SPY")
    assert actions == []


def test_decide_fomcvol_never_opens_when_chain_not_live():
    entry_day = date(2026, 1, 27)
    window_now = datetime(2026, 1, 27, 15, 30, tzinfo=NEW_YORK)
    view = _make_chain_view("SPY", spot=500.0, today=entry_day, live=False)  # unlive chain

    market = SyntheticMarket(entry_day, window_now, chain_view=view)
    holdings = SyntheticHoldings()
    rules = FOMCVolRules()

    actions = decide_fomcvol(market, holdings, rules, "SPY")
    assert actions == []


def test_decide_fomcvol_enters_one_session_before_fomc():
    entry_day = date(2026, 1, 27)
    window_now = datetime(2026, 1, 27, 15, 30, tzinfo=NEW_YORK)
    view = _make_chain_view("SPY", spot=500.0, today=entry_day, live=True)

    equity = 100_000.0
    rules = FOMCVolRules(max_risk_pct=0.05, side="long", structure="iron_fly")
    market = SyntheticMarket(entry_day, window_now, chain_view=view)
    holdings = SyntheticHoldings(equity_usd=equity)

    actions = decide_fomcvol(market, holdings, rules, "SPY")
    assert len(actions) == 1
    action = actions[0]
    assert isinstance(action, Open)
    assert action.pick.underlying == "SPY"
    # Max loss budget <= max_risk_pct * equity
    assert action.max_risk_usd <= rules.max_risk_pct * equity
    assert action.max_risk_usd == pytest.approx(5000.0)


def test_decide_fomcvol_exits_after_announcement():
    fomc_day = date(2026, 1, 28)
    opened_day = date(2026, 1, 27)
    post_announcement_now = datetime(2026, 1, 28, 14, 30, tzinfo=NEW_YORK)
    view = _make_chain_view("SPY", spot=500.0, today=fomc_day, live=True)

    market = SyntheticMarket(fomc_day, post_announcement_now, chain_view=view)
    holdings = SyntheticHoldings(underlyings_held={"SPY"}, opened_date=opened_day)
    rules = FOMCVolRules()

    actions = decide_fomcvol(market, holdings, rules, "SPY")
    assert len(actions) == 1
    assert isinstance(actions[0], Close)
    assert actions[0].underlying == "SPY"
    assert actions[0].include_stock is True


def test_decide_fomcvol_delta_hedge():
    entry_day = date(2026, 1, 27)
    window_now = datetime(2026, 1, 27, 15, 30, tzinfo=NEW_YORK)
    view = _make_chain_view("SPY", spot=500.0, today=entry_day, live=True)

    rules = FOMCVolRules(delta_hedged=True)
    market = SyntheticMarket(entry_day, window_now, chain_view=view)
    holdings = SyntheticHoldings()

    actions = decide_fomcvol(market, holdings, rules, "SPY")
    assert len(actions) == 2
    assert isinstance(actions[0], Open)
    assert isinstance(actions[1], Hedge)
    assert actions[1].underlying == "SPY"


def test_option_fomc_vol_bot_init(mocker):
    mocker.patch("tradingbot.utils.botclass.init_db")
    mocker.patch("tradingbot.utils.bot_repository.BotRepository.create_or_get_bot")
    mocker.patch("tradingbot.utils.botclass.DataService")
    mocker.patch("tradingbot.utils.botclass.PortfolioManager")
    bot = OptionFOMCVolBot()
    assert bot.RULES.side == "long"
    assert bot.RULES.structure == "iron_fly"
    assert bot.RULES.wing_width_pct == 0.05
    assert bot.symbol == "SPY"
    assert RULES.structure == "iron_fly"
