"""
Tests for option_PreEarningsStraddleBot and utils/preearnings_straddle_rules.

Pure in-memory synthetic tests: no database connection or external services required.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from tradingbot.option_preearningsstraddlebot import OptionPreEarningsStraddleBot
from tradingbot.utils import options
from tradingbot.utils.option_decide import Close, Hedge, Holdings, Market, Open
from tradingbot.utils.preearnings_straddle_rules import (
    PreEarningsStraddleRules,
    decide_preearnings,
    in_entry_window,
)

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
        chains: dict[str, options.ChainView | None] | options.ChainView | None = None,
        earnings_events_map: dict[str, list[tuple[date, bool | None]]] | None = None,
    ):
        super().__init__(today, now)
        self._chains = chains
        self._earnings_events_map = earnings_events_map or {}

    def chain(self, underlying: str, target_dte: int) -> options.ChainView | None:
        if isinstance(self._chains, dict):
            if (underlying, target_dte) in self._chains:
                return self._chains[(underlying, target_dte)]
            return self._chains.get(underlying)
        return self._chains

    def vol_index(self, symbol: str) -> float | None:
        return 18.0

    def closes(self, underlying: str) -> pd.Series:
        return pd.Series([200.0], index=[self.today])

    def ohlc(self, symbols) -> dict[str, pd.DataFrame]:
        return {}

    def earnings_events(self, underlying: str) -> list[tuple[date, bool | None]]:
        return self._earnings_events_map.get(underlying, [])


class SyntheticHoldings(Holdings):
    """In-memory mock Holdings implementation."""

    def __init__(
        self,
        underlyings_held: set[str] | None = None,
        equity_usd: float = 100_000.0,
        opened_dates: dict[str, date] | date | None = None,
    ):
        self._underlyings = underlyings_held or set()
        self._equity = equity_usd
        self._opened_dates = opened_dates

    def underlyings(self) -> set[str]:
        return self._underlyings

    def book(self, underlying: str) -> options.OptionBook:
        return options.OptionBook(underlying, spot=200.0)

    def opened_on(self, underlying: str) -> date | None:
        if isinstance(self._opened_dates, dict):
            return self._opened_dates.get(underlying)
        return self._opened_dates if underlying in self._underlyings else None

    def flows_since(self, underlying: str, since: date) -> float:
        return 0.0

    def equity(self) -> float:
        return self._equity


def _make_chain_view(
    underlying: str = "AAPL",
    spot: float = 200.0,
    expiry: date | None = None,
    live: bool = True,
    today: date | None = None,
    iv: float = 0.25,
) -> options.ChainView:
    today = today or date(2026, 1, 20)
    expiry = expiry or date(2026, 2, 6)

    rows = []
    for strike in range(180, 225, 5):
        k = float(strike)
        for right in ("C", "P"):
            c_sym = f"{underlying}{expiry:%y%m%d}{right}{int(k * 1000):08d}"
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
                    "implied_volatility": iv,
                    "delta": 0.50 if k == spot else (0.10 if (right == "C" and k > spot) else -0.10),
                }
            )
    frame = pd.DataFrame(rows)
    return options.ChainView(underlying, expiry, frame, spot, live, today)


# ------------------------------------------------------------------
# Acceptance Tests
# ------------------------------------------------------------------


def test_rules_validation():
    rules = PreEarningsStraddleRules()
    assert rules.min_sessions_before == 5
    assert rules.max_sessions_before == 10
    assert rules.max_front_back_ratio == 1.15
    assert rules.max_risk_pct == 0.02
    assert rules.max_concurrent == 3
    assert rules.delta_band_usd == 2000.0
    assert rules.min_dte_after_report == 3

    with pytest.raises(ValueError):
        PreEarningsStraddleRules(min_sessions_before=0)
    with pytest.raises(ValueError):
        PreEarningsStraddleRules(min_sessions_before=10, max_sessions_before=5)
    with pytest.raises(ValueError):
        PreEarningsStraddleRules(max_risk_pct=0.0)


def test_acceptance_1_opens_straddle_7_sessions_out():
    """
    Acceptance test 1:
    Opens a straddle `Open` for a name whose report is 7 sessions out and sizes
    `max_risk_usd = 2%` of equity.
    """
    # 2026-01-20 is Tuesday. 7 sessions out is Thursday 2026-01-29.
    # sessions_between(2026-01-20, 2026-01-29) == 7.
    today = date(2026, 1, 20)
    report_date = date(2026, 1, 29)
    # after_close=False -> reaction_session is 2026-01-29 itself
    earnings_map = {"AAPL": [(report_date, False)]}

    # Expiry must be at least min_dte_after_report (3) past reaction (2026-01-29) -> 2026-02-06
    expiry = date(2026, 2, 6)
    view = _make_chain_view("AAPL", spot=200.0, expiry=expiry, today=today)

    now = datetime(2026, 1, 20, 15, 30, tzinfo=NEW_YORK)  # in entry window (30m to close)
    market = SyntheticMarket(today, now, chains=view, earnings_events_map=earnings_map)
    holdings = SyntheticHoldings(underlyings_held=set(), equity_usd=100_000.0)
    rules = PreEarningsStraddleRules()

    actions = decide_preearnings(market, holdings, rules, universe=["AAPL"])

    assert len(actions) == 1
    action = actions[0]
    assert isinstance(action, Open)
    assert action.pick.underlying == "AAPL"
    assert len(action.pick.legs) == 2
    # Check 1 ATM call and 1 ATM put
    rights = [leg[0][10] for leg in action.pick.legs]
    assert sorted(rights) == ["C", "P"]
    # Sized to 2% of $100,000 equity = $2,000
    assert action.max_risk_usd == pytest.approx(2000.0)


def test_acceptance_2_no_open_when_unknown_timing_too_close_or_held():
    """
    Acceptance test 2:
    Does NOT open when the timing is unknown (after_close is None),
    the report is 3 sessions out, or the book is already held.
    """
    today = date(2026, 1, 20)
    now = datetime(2026, 1, 20, 15, 30, tzinfo=NEW_YORK)
    view = _make_chain_view("AAPL", spot=200.0, expiry=date(2026, 2, 6), today=today)
    rules = PreEarningsStraddleRules()

    # Case 2a: Timing unknown (after_close is None)
    earnings_unknown = {"AAPL": [(date(2026, 1, 29), None)]}
    market = SyntheticMarket(today, now, chains=view, earnings_events_map=earnings_unknown)
    holdings = SyntheticHoldings(underlyings_held=set(), equity_usd=100_000.0)
    actions = decide_preearnings(market, holdings, rules, universe=["AAPL"])
    assert len(actions) == 0

    # Case 2b: Report is 3 sessions out (Friday 2026-01-23)
    # sessions_between(2026-01-20, 2026-01-23) == 3 < min_sessions_before (5)
    earnings_3sessions = {"AAPL": [(date(2026, 1, 23), False)]}
    market = SyntheticMarket(today, now, chains=view, earnings_events_map=earnings_3sessions)
    actions = decide_preearnings(market, holdings, rules, universe=["AAPL"])
    assert len(actions) == 0

    # Case 2c: Book is already held
    earnings_valid = {"AAPL": [(date(2026, 1, 29), False)]}
    market = SyntheticMarket(today, now, chains=view, earnings_events_map=earnings_valid)
    holdings_held = SyntheticHoldings(underlyings_held={"AAPL"}, equity_usd=100_000.0)
    actions = decide_preearnings(market, holdings_held, rules, universe=["AAPL"])
    # Should not open AAPL
    assert not any(isinstance(a, Open) and a.pick.underlying == "AAPL" for a in actions)


def test_acceptance_3_close_session_before_reaction_hedge_earlier():
    """
    Acceptance test 3:
    Returns Close(include_stock=True) on the session before the reaction session and Hedge earlier.
    """
    rules = PreEarningsStraddleRules(delta_band_usd=2000.0)
    # Reaction session is Wednesday 2026-01-28
    report_date = date(2026, 1, 28)
    earnings_map = {"AAPL": [(report_date, False)]}

    # Case 3a: 6 sessions earlier (Tuesday 2026-01-20) -> Hedge
    today_early = date(2026, 1, 20)
    now_early = datetime(2026, 1, 20, 15, 30, tzinfo=NEW_YORK)
    market_early = SyntheticMarket(today_early, now_early, earnings_events_map=earnings_map)
    holdings = SyntheticHoldings(
        underlyings_held={"AAPL"},
        equity_usd=100_000.0,
        opened_dates={"AAPL": today_early},
    )

    actions_early = decide_preearnings(market_early, holdings, rules, universe=["AAPL"])
    assert len(actions_early) == 1
    assert isinstance(actions_early[0], Hedge)
    assert actions_early[0].underlying == "AAPL"
    assert actions_early[0].band_usd == 2000.0

    # Case 3b: Session before reaction session (Tuesday 2026-01-27) -> Close(include_stock=True)
    # sessions_between(2026-01-27, 2026-01-28) == 1
    today_exit = date(2026, 1, 27)
    now_exit = datetime(2026, 1, 27, 15, 30, tzinfo=NEW_YORK)
    market_exit = SyntheticMarket(today_exit, now_exit, earnings_events_map=earnings_map)

    actions_exit = decide_preearnings(market_exit, holdings, rules, universe=["AAPL"])
    assert len(actions_exit) == 1
    assert isinstance(actions_exit[0], Close)
    assert actions_exit[0].underlying == "AAPL"
    assert actions_exit[0].include_stock is True

    # Case 3c: Reaction session itself or later -> Close(include_stock=True)
    today_rx = date(2026, 1, 28)
    now_rx = datetime(2026, 1, 28, 15, 30, tzinfo=NEW_YORK)
    market_rx = SyntheticMarket(today_rx, now_rx, earnings_events_map=earnings_map)

    actions_rx = decide_preearnings(market_rx, holdings, rules, universe=["AAPL"])
    assert len(actions_rx) == 1
    assert isinstance(actions_rx[0], Close)
    assert actions_rx[0].underlying == "AAPL"
    assert actions_rx[0].include_stock is True


def test_acceptance_4_broken_chain_does_not_suppress_next_entry():
    """
    Acceptance test 4:
    One broken chain (chain raises/None) does not suppress the entry on the next name.
    """
    today = date(2026, 1, 20)
    now = datetime(2026, 1, 20, 15, 30, tzinfo=NEW_YORK)
    report_date = date(2026, 1, 29)
    earnings_map = {
        "BROKEN": [(report_date, False)],
        "NONE_CHAIN": [(report_date, False)],
        "GOOD": [(report_date, False)],
    }

    view_good = _make_chain_view("GOOD", spot=200.0, expiry=date(2026, 2, 6), today=today)

    class BrokenChainMarket(SyntheticMarket):
        def chain(self, underlying: str, target_dte: int) -> options.ChainView | None:
            if underlying == "BROKEN":
                raise RuntimeError("Network timeout connecting to data feed")
            if underlying == "NONE_CHAIN":
                return None
            if underlying == "GOOD":
                return view_good
            return None

    market = BrokenChainMarket(today, now, earnings_events_map=earnings_map)
    holdings = SyntheticHoldings(underlyings_held=set(), equity_usd=100_000.0)
    rules = PreEarningsStraddleRules()

    actions = decide_preearnings(market, holdings, rules, universe=["BROKEN", "NONE_CHAIN", "GOOD"])

    # BROKEN raised and was logged, NONE_CHAIN returned None and was logged;
    # GOOD succeeded and produced an Open action!
    assert len(actions) == 1
    assert isinstance(actions[0], Open)
    assert actions[0].pick.underlying == "GOOD"


def test_iv_ratio_filter_skips_when_front_rich():
    """IV-ratio filter skips when front-expiry IV > max_front_back_ratio * back-expiry IV."""
    today = date(2026, 1, 20)
    now = datetime(2026, 1, 20, 15, 30, tzinfo=NEW_YORK)
    report_date = date(2026, 1, 29)
    earnings_map = {"AAPL": [(report_date, False)]}

    # Front chain at 2026-02-06 with high IV (0.35)
    # Back chain at 2026-02-13 with low IV (0.20) -> ratio = 1.75 > 1.15
    front_expiry = date(2026, 2, 6)
    back_expiry = date(2026, 2, 13)
    front_view = _make_chain_view("AAPL", spot=200.0, expiry=front_expiry, today=today, iv=0.35)
    back_view = _make_chain_view("AAPL", spot=200.0, expiry=back_expiry, today=today, iv=0.20)

    class MultiExpiryMarket(SyntheticMarket):
        def chain(self, underlying: str, target_dte: int) -> options.ChainView | None:
            if target_dte <= (front_expiry - today).days:
                return front_view
            return back_view

    market = MultiExpiryMarket(today, now, earnings_events_map=earnings_map)
    holdings = SyntheticHoldings(underlyings_held=set(), equity_usd=100_000.0)
    rules = PreEarningsStraddleRules(max_front_back_ratio=1.15)

    actions = decide_preearnings(market, holdings, rules, universe=["AAPL"])
    assert len(actions) == 0


def test_entry_window_filter():
    """Outside the 2h entry window before close, no entries are opened."""
    today = date(2026, 1, 20)
    # 11:00 NY is 300 min to close (> 120 min)
    now_early = datetime(2026, 1, 20, 11, 0, tzinfo=NEW_YORK)
    assert in_entry_window(now_early) is False

    view = _make_chain_view("AAPL", spot=200.0, expiry=date(2026, 2, 6), today=today)
    earnings_map = {"AAPL": [(date(2026, 1, 29), False)]}
    market = SyntheticMarket(today, now_early, chains=view, earnings_events_map=earnings_map)
    holdings = SyntheticHoldings(underlyings_held=set(), equity_usd=100_000.0)
    rules = PreEarningsStraddleRules()

    actions = decide_preearnings(market, holdings, rules, universe=["AAPL"])
    assert len(actions) == 0


def test_bot_instantiation_and_properties(mocker):
    """OptionPreEarningsStraddleBot instantiates cleanly and provides expected properties."""
    mocker.patch("tradingbot.utils.botclass.init_db")
    mocker.patch("tradingbot.utils.bot_repository.BotRepository.create_or_get_bot")
    mocker.patch("tradingbot.utils.botclass.DataService")
    mocker.patch("tradingbot.utils.botclass.PortfolioManager")
    bot = OptionPreEarningsStraddleBot()
    assert bot.bot_name == "option_PreEarningsStraddleBot"
    assert bot.universe() == OptionPreEarningsStraddleBot.UNIVERSE
    assert bot.RULES.min_sessions_before == 5
