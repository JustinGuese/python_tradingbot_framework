"""
Tests for option_SkewBWBBot and utils/skew_bwb_rules.

Pure in-memory synthetic tests: no database connection or external network required.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from tradingbot.option_skewbwbbot import OptionSkewBWBBot
from tradingbot.utils import option_math as om
from tradingbot.utils import options
from tradingbot.utils.option_decide import Close, Holdings, Market, Open
from tradingbot.utils.skew_bwb_rules import (
    SkewBWBRules,
    decide_skewbwb,
    put_skew,
    select_put_bwb,
)
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
        vix: float | None = 16.0,
        vix3m: float | None = 18.0,
    ):
        super().__init__(today, now)
        self._chain_view = chain_view
        self._vol_indices: dict[str, float | None] = {
            VIX: vix,
            VIX3M: vix3m,
        }

    def chain(self, underlying: str, target_dte: int) -> options.ChainView | None:
        return self._chain_view

    def vol_index(self, symbol: str) -> float | None:
        return self._vol_indices.get(symbol)

    def closes(self, underlying: str) -> pd.Series:
        return pd.Series([500.0], index=[self.today])

    def ohlc(self, symbols) -> dict[str, pd.DataFrame]:
        return {}


class SyntheticHoldings(Holdings):
    """In-memory mock Holdings implementation."""

    def __init__(
        self,
        underlyings_held: set[str] | None = None,
        equity_usd: float = 100_000.0,
        book: options.OptionBook | None = None,
        opened_date: date | None = None,
    ):
        self._underlyings = underlyings_held or set()
        self._equity = equity_usd
        self._book = book
        self._opened_on = opened_date

    def underlyings(self) -> set[str]:
        return self._underlyings

    def book(self, underlying: str) -> options.OptionBook:
        if self._book is not None:
            return self._book
        return options.OptionBook(underlying, spot=500.0, today=date(2026, 1, 27))

    def opened_on(self, underlying: str) -> date | None:
        return self._opened_on if underlying in self._underlyings else None

    def flows_since(self, underlying: str, since: date) -> float:
        return 0.0

    def equity(self) -> float:
        return self._equity


def _make_chain(
    underlying: str = "SPY",
    spot: float = 500.0,
    today: date | None = None,
    expiry: date | None = None,
    live: bool = True,
    skew_slope: float = 0.0035,
    base_iv: float = 0.20,
    r: float = 0.04,
) -> options.ChainView:
    """Generate a synthetic chain with configurable put skew."""
    today = today or date(2026, 1, 27)  # Tuesday, NYSE session
    expiry = expiry or date(2026, 3, 3)  # 35 DTE
    T = om.year_fraction(expiry, today)

    rows = []
    for strike in range(400, 560, 5):
        k = float(strike)
        for right in ("C", "P"):
            c_sym = f"{underlying}{expiry:%y%m%d}{right}{int(k * 1000):08d}"
            sig = base_iv + skew_slope * max(0.0, spot - k) if right == "P" else base_iv
            px = om.bs_price(spot, k, T, r, sig, right)
            d = om.delta(spot, k, T, r, sig, right)
            rows.append(
                {
                    "contract_symbol": c_sym,
                    "underlying": underlying,
                    "expiration": expiry,
                    "option_type": right,
                    "strike": k,
                    "bid": px - 0.05,
                    "ask": px + 0.05,
                    "last_price": px,
                    "volume": 100,
                    "open_interest": 1000,
                    "implied_volatility": sig,
                    "delta": d,
                }
            )
    frame = pd.DataFrame(rows)
    return options.ChainView(underlying, expiry, frame, spot, live, today)


def _make_held_book(
    underlying: str = "SPY",
    spot: float = 500.0,
    today: date = date(2026, 1, 27),
    expiry: date = date(2026, 3, 3),
    entry_value: float = -200.0,  # $200 credit received
    mark_value: float = -100.0,  # $100 mid to close -> $100 profit
) -> options.OptionBook:
    """Construct an OptionBook with one synthetic short position."""
    c = options.OptionContract(underlying, expiry, "P", 480.0)
    key = f"{underlying}{expiry:%y%m%d}P00480000"
    pos = options.OptionPosition(
        key=key,
        contract=c,
        qty=-100.0,  # 1 short contract (-100 shares)
        price=-mark_value / 100.0,  # mark price
        iv=0.20,
        greeks=om.ZERO_GREEKS,
    )
    return options.OptionBook(
        underlying=underlying,
        spot=spot,
        today=today,
        positions=[pos],
        entry_value=entry_value,
    )


# ------------------------------------------------------------------
# Acceptance Test 1: select_put_bwb contract structure & credit
# ------------------------------------------------------------------


def test_select_put_bwb_descending_strikes_and_credit():
    """
    Acceptance test 1:
    select_put_bwb returns legs [+1, -2, +1] with strictly descending strikes
    and a net credit on a synthetic skewed chain; raises when no credit.
    """
    rules = SkewBWBRules()
    skewed_view = _make_chain(skew_slope=0.0035, base_iv=0.20)

    # 1. Successful construction on skewed chain
    pick = select_put_bwb(skewed_view, rules)
    assert len(pick.legs) == 3

    # Check legs lots: [+1, -2, +1]
    lots = [lot for _, lot in pick.legs]
    assert lots == [1, -2, 1]

    # Check strictly descending strikes: K1 > K2 > K3
    contracts = [options.parse_occ(sym) for sym, _ in pick.legs]
    k1, k2, k3 = contracts[0].strike, contracts[1].strike, contracts[2].strike
    assert k1 > k2 > k3, f"Strikes not strictly descending: K1={k1}, K2={k2}, K3={k3}"

    # Verify net credit
    priced = options.priced_side(skewed_view, "P").set_index("contract_symbol")
    p1 = float(priced.loc[pick.legs[0][0], "price"])
    p2 = float(priced.loc[pick.legs[1][0], "price"])
    p3 = float(priced.loc[pick.legs[2][0], "price"])
    net_credit = 2 * p2 - p1 - p3
    assert net_credit > 0, f"Expected positive net credit, got {net_credit}"

    # 2. Raises ValueError when there is no credit (flat volatility surface)
    flat_view = _make_chain(skew_slope=0.0, base_iv=0.20)
    with pytest.raises(ValueError, match="not a net credit"):
        select_put_bwb(flat_view, rules)


# ------------------------------------------------------------------
# Acceptance Test 2: Entry conditions & term ratio gating
# ------------------------------------------------------------------


def test_decide_skewbwb_entry_conditions():
    """
    Acceptance test 2:
    Entry fires only when skew >= threshold AND term ratio gate passes;
    no entry otherwise; no second entry when held.
    """
    rules = SkewBWBRules(min_skew=0.04, max_term_ratio=1.0, max_risk_pct=0.03)
    today = date(2026, 1, 27)  # Tuesday session
    now = datetime(2026, 1, 27, 16, 20, tzinfo=NEW_YORK)

    skewed_view = _make_chain(skew_slope=0.0035, base_iv=0.20, today=today)
    assert put_skew(skewed_view) >= rules.min_skew

    # 1. Valid entry: skew >= 0.04, term ratio 16/18 = 0.889 <= 1.0, flat book
    market_ok = SyntheticMarket(today, now, chain_view=skewed_view, vix=16.0, vix3m=18.0)
    holdings_flat = SyntheticHoldings(equity_usd=100_000.0)
    actions = decide_skewbwb(market_ok, holdings_flat, rules, "SPY")

    assert len(actions) == 1
    action = actions[0]
    assert isinstance(action, Open)
    assert action.max_risk_usd == pytest.approx(3000.0)  # 3% of 100k
    assert [lot for _, lot in action.pick.legs] == [1, -2, 1]

    # 2. Fails when skew < min_skew (flat chain)
    flat_view = _make_chain(skew_slope=0.0, base_iv=0.20, today=today)
    market_flat_vol = SyntheticMarket(today, now, chain_view=flat_view, vix=16.0, vix3m=18.0)
    assert decide_skewbwb(market_flat_vol, holdings_flat, rules, "SPY") == []

    # 3. Fails when term ratio gate fails (inverted term structure: VIX=22, VIX3M=18 -> ratio=1.22 > 1.0)
    market_inverted = SyntheticMarket(today, now, chain_view=skewed_view, vix=22.0, vix3m=18.0)
    assert decide_skewbwb(market_inverted, holdings_flat, rules, "SPY") == []

    # 4. Fails when term ratio is missing
    market_no_term = SyntheticMarket(today, now, chain_view=skewed_view, vix=None, vix3m=18.0)
    assert decide_skewbwb(market_no_term, holdings_flat, rules, "SPY") == []

    # 5. Fails when chain is unlive
    unlive_view = _make_chain(skew_slope=0.0035, base_iv=0.20, today=today, live=False)
    market_unlive = SyntheticMarket(today, now, chain_view=unlive_view, vix=16.0, vix3m=18.0)
    assert decide_skewbwb(market_unlive, holdings_flat, rules, "SPY") == []

    # 6. Fails outside NYSE session
    saturday = date(2026, 1, 24)
    market_weekend = SyntheticMarket(saturday, now, chain_view=skewed_view, vix=16.0, vix3m=18.0)
    assert decide_skewbwb(market_weekend, holdings_flat, rules, "SPY") == []

    # 7. No second entry when already held
    book = _make_held_book(today=today, entry_value=-200.0, mark_value=-150.0)  # 25% profit, DTE > 14
    holdings_held = SyntheticHoldings(underlyings_held={"SPY"}, book=book)
    assert decide_skewbwb(market_ok, holdings_held, rules, "SPY") == []


# ------------------------------------------------------------------
# Acceptance Test 3: Exit triggers
# ------------------------------------------------------------------


def test_decide_skewbwb_exit_triggers():
    """
    Acceptance test 3:
    Exits on take-profit, on DTE <= exit_dte, and on inverted term structure.
    """
    rules = SkewBWBRules(take_profit=0.50, exit_dte=14, max_term_ratio=1.0)
    today = date(2026, 1, 27)
    now = datetime(2026, 1, 27, 16, 20, tzinfo=NEW_YORK)

    # Base market: term structure normal (16 / 18 = 0.889)
    market = SyntheticMarket(today, now, vix=16.0, vix3m=18.0)

    # 1. Take profit exit: entry_value = -200, mark = -80 -> pnl = +120 -> pnl_pct = 60% >= 50%
    book_tp = _make_held_book(today=today, expiry=date(2026, 3, 3), entry_value=-200.0, mark_value=-80.0)
    assert book_tp.pnl_pct >= 0.50
    actions_tp = decide_skewbwb(market, SyntheticHoldings({"SPY"}, book=book_tp), rules, "SPY")
    assert len(actions_tp) == 1
    assert isinstance(actions_tp[0], Close)
    assert "take profit" in actions_tp[0].reason

    # 2. DTE exit: expiry is 10 days away <= 14 DTE, pnl_pct = 10% < 50%
    book_dte = _make_held_book(
        today=today,
        expiry=date(2026, 2, 6),  # 10 days out
        entry_value=-200.0,
        mark_value=-180.0,  # 10% profit
    )
    assert book_dte.dte is not None and book_dte.dte <= rules.exit_dte
    actions_dte = decide_skewbwb(market, SyntheticHoldings({"SPY"}, book=book_dte), rules, "SPY")
    assert len(actions_dte) == 1
    assert isinstance(actions_dte[0], Close)
    assert "DTE" in actions_dte[0].reason

    # 3. Inverted term structure exit: VIX=25, VIX3M=20 -> ratio=1.25 > 1.0
    market_inverted = SyntheticMarket(today, now, vix=25.0, vix3m=20.0)
    book_normal = _make_held_book(
        today=today,
        expiry=date(2026, 3, 3),  # 35 DTE
        entry_value=-200.0,
        mark_value=-180.0,  # 10% profit
    )
    actions_unwind = decide_skewbwb(market_inverted, SyntheticHoldings({"SPY"}, book=book_normal), rules, "SPY")
    assert len(actions_unwind) == 1
    assert isinstance(actions_unwind[0], Close)
    assert "term ratio" in actions_unwind[0].reason
    assert "inverted" in actions_unwind[0].reason

    # 4. Normal hold: no exit triggered
    actions_hold = decide_skewbwb(market, SyntheticHoldings({"SPY"}, book=book_normal), rules, "SPY")
    assert actions_hold == []


# ------------------------------------------------------------------
# Bot Class Wiring
# ------------------------------------------------------------------


def test_option_skewbwbbot_initialization(mocker):
    """Verify OptionSkewBWBBot initializes cleanly with rules."""
    mocker.patch("tradingbot.utils.botclass.init_db")
    mocker.patch("tradingbot.utils.bot_repository.BotRepository.create_or_get_bot")
    mocker.patch("tradingbot.utils.botclass.DataService")
    mocker.patch("tradingbot.utils.botclass.PortfolioManager")

    bot = OptionSkewBWBBot()
    assert bot.bot_name == "option_SkewBWBBot"
    assert bot.symbol == "SPY"
    assert bot.RULES.underlying == "SPY"
    assert bot.RULES.target_dte == 35
    assert bot.RULES.min_skew == 0.04
    assert bot.RULES.max_term_ratio == 1.0
