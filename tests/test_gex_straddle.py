"""
Tests for option_GexStraddleBot and utils/gex_straddle_rules.

Pure in-memory synthetic tests: no database connection or external services required.
Covers acceptance tests 1-4 for Step C of PLAN-optbots.md.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from tradingbot.option_gexstraddlebot import RULES, OptionGexStraddleBot
from tradingbot.utils import options
from tradingbot.utils.gex_straddle_rules import (
    GexStraddleRules,
    chain_gex,
    decide_gexstraddle,
)
from tradingbot.utils.option_decide import Close, Holdings, Market, Open
from tradingbot.utils.vol_indices import VIX, VIX3M

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
        vol_indices: dict[str, float] | None = None,
    ):
        super().__init__(today, now)
        self._chain_view = chain_view
        self._closes = closes_series if closes_series is not None else pd.Series([500.0], index=[today])
        self._vol_indices = vol_indices or {}

    def chain(self, underlying: str, target_dte: int) -> options.ChainView | None:
        return self._chain_view

    def vol_index(self, symbol: str) -> float | None:
        return self._vol_indices.get(symbol, None)

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
        book: options.OptionBook | None = None,
    ):
        self._underlyings = underlyings_held or set()
        self._equity = equity_usd
        self._opened_on = opened_date
        self._book = book

    def underlyings(self) -> set[str]:
        return self._underlyings

    def book(self, underlying: str) -> options.OptionBook:
        if self._book is not None:
            return self._book
        return options.OptionBook(underlying, spot=500.0, today=date(2026, 1, 28))

    def opened_on(self, underlying: str) -> date | None:
        return self._opened_on

    def flows_since(self, underlying: str, since: date) -> float:
        return 0.0

    def equity(self) -> float:
        return self._equity


def _make_chain_view(
    underlying: str = "SPY",
    spot: float = 500.0,
    expiry: date | None = None,
    today: date | None = None,
    live: bool = True,
    call_oi: int = 1000,
    put_oi: int = 1000,
    gamma: float = 0.05,
    has_oi: bool = True,
) -> options.ChainView:
    today = today or date(2026, 1, 28)
    expiry = expiry or date(2026, 1, 28)

    rows = []
    for strike in range(480, 525, 5):
        k = float(strike)
        for right in ("C", "P"):
            c_sym = f"{underlying}{expiry:%y%m%d}{right}{int(k * 1000):08d}"
            intrinsic = max(0.0, spot - k if right == "C" else k - spot)
            mid = max(intrinsic, 3.0)
            oi = (call_oi if right == "C" else put_oi) if has_oi else 0
            row = {
                "contract_symbol": c_sym,
                "underlying": underlying,
                "expiration": expiry,
                "option_type": right,
                "strike": k,
                "bid": mid - 0.05,
                "ask": mid + 0.05,
                "last_price": mid,
                "volume": 100,
                "open_interest": oi,
                "implied_volatility": 0.20,
                "delta": 0.50 if k == spot else (0.10 if (right == "C" and k > spot) else -0.10),
                "gamma": gamma,
            }
            if not has_oi:
                del row["open_interest"]
            rows.append(row)
    frame = pd.DataFrame(rows)
    return options.ChainView(underlying, expiry, frame, spot, live, today)


# ------------------------------------------------------------------
# Acceptance Test 1: chain_gex
# ------------------------------------------------------------------


def test_chain_gex_put_call_heavy_and_missing_oi():
    """Acceptance 1: chain_gex is negative for put-heavy, positive for call-heavy, None without OI."""
    today = date(2026, 1, 28)

    # Call-heavy chain (call OI >> put OI) -> GEX > 0
    view_call_heavy = _make_chain_view(today=today, expiry=today, call_oi=10000, put_oi=100)
    gex_call = chain_gex(view_call_heavy)
    assert gex_call is not None
    assert gex_call > 0

    # Put-heavy chain (put OI >> call OI) -> GEX < 0
    view_put_heavy = _make_chain_view(today=today, expiry=today, call_oi=100, put_oi=10000)
    gex_put = chain_gex(view_put_heavy)
    assert gex_put is not None
    assert gex_put < 0

    # Missing open_interest column -> None
    view_no_oi = _make_chain_view(today=today, expiry=today, has_oi=False)
    assert chain_gex(view_no_oi) is None

    # All zero open_interest -> None
    view_zero_oi = _make_chain_view(today=today, expiry=today, call_oi=0, put_oi=0)
    assert chain_gex(view_zero_oi) is None


# ------------------------------------------------------------------
# Acceptance Test 2: Entry conditions
# ------------------------------------------------------------------


def test_decide_gexstraddle_entry_conditions():
    """Acceptance 2: Enters in 10:00-11:00 NY only when GEX < 0 and ratio > 1; no entry outside."""
    session_day = date(2026, 1, 28)  # Wednesday
    t_1030 = datetime(2026, 1, 28, 10, 30, tzinfo=NEW_YORK)
    rules = GexStraddleRules(max_risk_pct=0.005)

    # Put-heavy live 0DTE chain -> GEX < 0
    view_valid = _make_chain_view(today=session_day, expiry=session_day, call_oi=100, put_oi=10000, live=True)

    # 1. Base valid entry
    market = SyntheticMarket(
        session_day,
        t_1030,
        chain_view=view_valid,
        vol_indices={VIX: 22.0, VIX3M: 20.0},  # ratio = 1.1 > 1.0
    )
    holdings = SyntheticHoldings(equity_usd=100_000.0)
    actions = decide_gexstraddle(market, holdings, rules, "SPY")
    assert len(actions) == 1
    assert isinstance(actions[0], Open)
    assert actions[0].pick.underlying == "SPY"
    assert actions[0].max_risk_usd == pytest.approx(500.0)  # 0.5% of 100k
    assert "GEX straddle entry" in actions[0].reason

    # 2. Outside window: before 10:00 NY (09:45 NY)
    t_0945 = datetime(2026, 1, 28, 9, 45, tzinfo=NEW_YORK)
    market_early = SyntheticMarket(session_day, t_0945, chain_view=view_valid, vol_indices={VIX: 22.0, VIX3M: 20.0})
    assert decide_gexstraddle(market_early, holdings, rules, "SPY") == []

    # 3. Outside window: after 11:00 NY (11:15 NY)
    t_1115 = datetime(2026, 1, 28, 11, 15, tzinfo=NEW_YORK)
    market_late = SyntheticMarket(session_day, t_1115, chain_view=view_valid, vol_indices={VIX: 22.0, VIX3M: 20.0})
    assert decide_gexstraddle(market_late, holdings, rules, "SPY") == []

    # 4. GEX >= 0 (call-heavy chain)
    view_call_heavy = _make_chain_view(today=session_day, expiry=session_day, call_oi=10000, put_oi=100, live=True)
    market_pos_gex = SyntheticMarket(
        session_day, t_1030, chain_view=view_call_heavy, vol_indices={VIX: 22.0, VIX3M: 20.0}
    )
    assert decide_gexstraddle(market_pos_gex, holdings, rules, "SPY") == []

    # 5. Term ratio <= 1.0 (VIX <= VIX3M)
    market_flat_term = SyntheticMarket(session_day, t_1030, chain_view=view_valid, vol_indices={VIX: 18.0, VIX3M: 20.0})
    assert decide_gexstraddle(market_flat_term, holdings, rules, "SPY") == []

    # 6. Non-0DTE expiry (expiry tomorrow)
    tomorrow = date(2026, 1, 29)
    view_non_0dte = _make_chain_view(today=session_day, expiry=tomorrow, call_oi=100, put_oi=10000, live=True)
    market_non_0dte = SyntheticMarket(
        session_day, t_1030, chain_view=view_non_0dte, vol_indices={VIX: 22.0, VIX3M: 20.0}
    )
    assert decide_gexstraddle(market_non_0dte, holdings, rules, "SPY") == []

    # 7. Unlive chain
    view_unlive = _make_chain_view(today=session_day, expiry=session_day, call_oi=100, put_oi=10000, live=False)
    market_unlive = SyntheticMarket(session_day, t_1030, chain_view=view_unlive, vol_indices={VIX: 22.0, VIX3M: 20.0})
    assert decide_gexstraddle(market_unlive, holdings, rules, "SPY") == []


# ------------------------------------------------------------------
# Acceptance Test 3: opened_on guard (no re-entry on same session)
# ------------------------------------------------------------------


def test_decide_gexstraddle_no_reentry_same_session():
    """Acceptance 3: Does not re-enter the same session after an exit (opened_on guard)."""
    session_day = date(2026, 1, 28)
    t_1030 = datetime(2026, 1, 28, 10, 30, tzinfo=NEW_YORK)
    rules = GexStraddleRules()
    view = _make_chain_view(today=session_day, expiry=session_day, call_oi=100, put_oi=10000, live=True)
    market = SyntheticMarket(session_day, t_1030, chain_view=view, vol_indices={VIX: 22.0, VIX3M: 20.0})

    # Flat position, but opened earlier today
    holdings_reentry = SyntheticHoldings(underlyings_held=set(), opened_date=session_day)
    actions = decide_gexstraddle(market, holdings_reentry, rules, "SPY")
    assert actions == []

    # Flat book (opened_on is None once closed), but the trade log shows an entry today
    holdings_flat = SyntheticHoldings(underlyings_held=set(), opened_date=None)
    assert decide_gexstraddle(market, holdings_flat, rules, "SPY", entered_today=True) == []
    assert any(isinstance(a, Open) for a in decide_gexstraddle(market, holdings_flat, rules, "SPY"))


# ------------------------------------------------------------------
# Acceptance Test 4: Exits
# ------------------------------------------------------------------


class MockBook:
    def __init__(self, pnl_pct: float = 0.0):
        self.pnl_pct = pnl_pct


def test_decide_gexstraddle_exits():
    """Acceptance 4: Exits at +100% and force-closes after 15:45 NY, including when held on a later session."""
    session_day = date(2026, 1, 28)
    t_1030 = datetime(2026, 1, 28, 10, 30, tzinfo=NEW_YORK)
    rules = GexStraddleRules(take_profit=1.0, flat_time_ny=time(15, 45))
    market_morning = SyntheticMarket(session_day, t_1030)

    # 1. Take profit exit at +100% PnL during normal window
    book_tp = MockBook(pnl_pct=1.0)
    holdings_tp = SyntheticHoldings(underlyings_held={"SPY"}, opened_date=session_day, book=book_tp)
    actions_tp = decide_gexstraddle(market_morning, holdings_tp, rules, "SPY")
    assert len(actions_tp) == 1
    assert isinstance(actions_tp[0], Close)
    assert actions_tp[0].underlying == "SPY"
    assert actions_tp[0].include_stock is True
    assert "Take profit" in actions_tp[0].reason

    # 2. Force close after 15:45 NY on the same session
    t_1550 = datetime(2026, 1, 28, 15, 50, tzinfo=NEW_YORK)
    market_eod = SyntheticMarket(session_day, t_1550)
    book_normal = MockBook(pnl_pct=0.04)  # +4% pnl, below take profit
    holdings_eod = SyntheticHoldings(underlyings_held={"SPY"}, opened_date=session_day, book=book_normal)
    actions_eod = decide_gexstraddle(market_eod, holdings_eod, rules, "SPY")
    assert len(actions_eod) == 1
    assert isinstance(actions_eod[0], Close)
    assert actions_eod[0].include_stock is True
    assert "Flat by 15:45 NY" in actions_eod[0].reason

    # 3. Force close holdover found held on a later session (opened yesterday)
    next_day = date(2026, 1, 29)
    market_next_day = SyntheticMarket(next_day, t_1030)  # morning on next day
    holdings_holdover = SyntheticHoldings(underlyings_held={"SPY"}, opened_date=session_day, book=book_normal)
    actions_holdover = decide_gexstraddle(market_next_day, holdings_holdover, rules, "SPY")
    assert len(actions_holdover) == 1
    assert isinstance(actions_holdover[0], Close)
    assert actions_holdover[0].include_stock is True
    assert "Holdover from prior session" in actions_holdover[0].reason


# ------------------------------------------------------------------
# Bot Class and Rules Tests
# ------------------------------------------------------------------


def test_option_gex_straddle_bot_init(mocker):
    mocker.patch("tradingbot.utils.botclass.init_db")
    mocker.patch("tradingbot.utils.bot_repository.BotRepository.create_or_get_bot")
    mocker.patch("tradingbot.utils.botclass.DataService")
    mocker.patch("tradingbot.utils.botclass.PortfolioManager")
    bot = OptionGexStraddleBot()
    assert bot.symbol == "SPY"
    assert bot.RULES.underlying == "SPY"
    assert bot.RULES.take_profit == 1.0
    assert bot.RULES.max_risk_pct == 0.005
    assert RULES.underlying == "SPY"


def test_gex_straddle_rules_validation():
    with pytest.raises(ValueError, match="max_risk_pct"):
        GexStraddleRules(max_risk_pct=0.0)
    with pytest.raises(ValueError, match="take_profit"):
        GexStraddleRules(take_profit=-0.1)
    with pytest.raises(ValueError, match="entry_not_before_ny"):
        GexStraddleRules(entry_not_before_ny=time(12, 0), entry_not_after_ny=time(11, 0))
    with pytest.raises(ValueError, match="flat_time_ny"):
        GexStraddleRules(entry_not_after_ny=time(16, 0), flat_time_ny=time(15, 45))
