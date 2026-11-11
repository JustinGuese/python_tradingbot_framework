"""
Unit tests for PutWriteRules and decide_putwrite.

Tests:
  - No entry when VIX/VIX3M exceeds the gate or close < SMA200 with the filter on.
  - Cash-secured sizing never exceeds free cash.
  - Take-profit and exit-DTE close.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
import pytest

from tradingbot.utils import option_math as om
from tradingbot.utils import options
from tradingbot.utils.option_decide import Action, Close, Holdings, Market, Open
from tradingbot.utils.putwrite_rules import (
    PutWriteRules,
    decide_putwrite,
    get_available_cash,
    putwrite_entry_ok,
    putwrite_exit_reason,
)


class FakeMarket(Market):
    def __init__(
        self,
        today: date = date(2026, 1, 15),
        vix: float | None = 18.0,
        vix3m: float | None = 20.0,
        close_price: float = 500.0,
        closes_series: pd.Series | None = None,
        chain_view: options.ChainView | None = None,
    ):
        super().__init__(today, datetime.now(timezone.utc))
        self._vix = vix
        self._vix3m = vix3m
        self._close_price = close_price
        if closes_series is None:
            # 250 days of prices rising slightly to close_price
            dates = pd.date_range(end=pd.Timestamp(today), periods=250, freq="B")
            prices = np.linspace(close_price * 0.9, close_price, 250)
            self._closes = pd.Series(prices, index=dates)
        else:
            self._closes = closes_series
        self._chain = chain_view

    def chain(self, underlying: str, target_dte: int) -> options.ChainView | None:
        return self._chain

    def vol_index(self, symbol: str) -> float | None:
        if symbol == "^VIX":
            return self._vix
        if symbol == "^VIX3M":
            return self._vix3m
        return None

    def closes(self, underlying: str) -> pd.Series:
        return self._closes

    def ohlc(self, symbols) -> dict[str, pd.DataFrame]:
        return {}


class FakeHoldings(Holdings):
    def __init__(self, cash: float = 100_000.0, book: options.OptionBook | None = None):
        self._cash = cash
        self._book = book

    @property
    def cash(self) -> float:
        return self._cash

    def underlyings(self) -> set[str]:
        if self._book is not None and not self._book.empty:
            return {self._book.underlying}
        return set()

    def book(self, underlying: str) -> options.OptionBook:
        if self._book is not None and self._book.underlying == underlying:
            return self._book
        return options.OptionBook(
            underlying=underlying,
            spot=500.0,
            today=date(2026, 1, 15),
            positions=[],
            entry_value=0.0,
            greeks=om.Greeks(0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            shares=0.0,
        )

    def opened_on(self, underlying: str) -> date | None:
        return None

    def flows_since(self, underlying: str, since: date) -> float:
        return 0.0

    def equity(self) -> float:
        val = self._book.value if self._book else 0.0
        return self._cash + val


def _make_chain(
    underlying: str = "SPY",
    spot: float = 500.0,
    strikes: list[float] | None = None,
    today: date = date(2026, 1, 15),
    expiry: date = date(2026, 2, 20),
    live: bool = True,
) -> options.ChainView:
    if strikes is None:
        strikes = [480.0, 485.0, 490.0, 495.0, 500.0, 505.0]
    rows = []
    for k in strikes:
        sym = f"{underlying}{expiry:%y%m%d}P{round(k * 1000):08d}"
        # Approximate delta: -0.50 ATM, -0.25 at 490, etc.
        delta = -0.50 + (k - spot) * 0.015
        bid = max(5.0 - (spot - k) * 0.1, 0.5)
        ask = bid + 0.10
        rows.append(
            {
                "contract_symbol": sym,
                "option_type": "P",
                "strike": k,
                "bid": bid,
                "ask": ask,
                "last_price": (bid + ask) / 2,
                "delta": delta,
                "implied_volatility": 0.20,
            }
        )
    df = pd.DataFrame(rows)
    return options.ChainView(underlying, expiry, df, spot, live, today)


# ------------------------------------------------------------------
# Gate Tests
# ------------------------------------------------------------------


def test_no_entry_when_vix_vix3m_exceeds_gate():
    """No entry when VIX/VIX3M > max_term_ratio, or when ratio is missing."""
    chain = _make_chain(spot=500.0)
    holdings = FakeHoldings(cash=100_000.0)

    # Inverted curve: VIX=25, VIX3M=20 -> ratio 1.25 > 1.0
    market_inv = FakeMarket(vix=25.0, vix3m=20.0, chain_view=chain)
    rules_gated = PutWriteRules(max_term_ratio=1.0)
    actions = decide_putwrite(market_inv, holdings, rules_gated, "SPY")
    assert actions == [], "Must not enter when VIX/VIX3M exceeds max_term_ratio gate"

    # Calm curve: VIX=18, VIX3M=20 -> ratio 0.90 <= 1.0
    market_calm = FakeMarket(vix=18.0, vix3m=20.0, chain_view=chain)
    actions_calm = decide_putwrite(market_calm, holdings, rules_gated, "SPY")
    assert len(actions_calm) == 1, "Should enter when VIX/VIX3M is below gate"
    assert isinstance(actions_calm[0], Open)

    # Missing VIX3M quote when gate is active -> blocks entry
    market_missing = FakeMarket(vix=18.0, vix3m=None, chain_view=chain)
    actions_missing = decide_putwrite(market_missing, holdings, rules_gated, "SPY")
    assert actions_missing == [], "Missing term quote must block entry when gate is active"

    # Gate inactive (max_term_ratio=None) -> enters even with inverted curve
    rules_ungated = PutWriteRules(max_term_ratio=None)
    actions_ungated = decide_putwrite(market_inv, holdings, rules_ungated, "SPY")
    assert len(actions_ungated) == 1, "Ungated rules should ignore term curve"


def test_no_entry_when_close_below_sma200_with_trend_filter():
    """No entry when close <= SMA200 if trend_filter is True."""
    chain = _make_chain(spot=500.0)
    holdings = FakeHoldings(cash=100_000.0)
    today = date(2026, 1, 15)

    # Down-trend: 250 days dropping from 600 to 450 (close 450 < SMA200 ~525)
    dates = pd.date_range(end=pd.Timestamp(today), periods=250, freq="B")
    down_prices = pd.Series(np.linspace(600, 450, 250), index=dates)
    market_down = FakeMarket(today=today, close_price=450.0, closes_series=down_prices, chain_view=chain)

    rules_trend = PutWriteRules(trend_filter=True)
    actions_down = decide_putwrite(market_down, holdings, rules_trend, "SPY")
    assert actions_down == [], "Must not enter when close < SMA200 and trend filter is on"

    # Up-trend: 250 days rising from 400 to 550 (close 550 > SMA200 ~475)
    up_prices = pd.Series(np.linspace(400, 550, 250), index=dates)
    market_up = FakeMarket(today=today, close_price=550.0, closes_series=up_prices, chain_view=chain)
    actions_up = decide_putwrite(market_up, holdings, rules_trend, "SPY")
    assert len(actions_up) == 1, "Should enter when close > SMA200 and trend filter is on"

    # Trend filter off: enters even in downtrend
    rules_no_trend = PutWriteRules(trend_filter=False)
    actions_no_trend = decide_putwrite(market_down, holdings, rules_no_trend, "SPY")
    assert len(actions_no_trend) == 1, "Should enter when trend filter is disabled"


# ------------------------------------------------------------------
# Sizing Tests
# ------------------------------------------------------------------


def test_cash_secured_sizing_never_exceeds_free_cash():
    """Cash-secured put sizing: cash covers strike x 100, never exceeding free cash."""
    today = date(2026, 1, 15)
    expiry = date(2026, 2, 20)
    strike = 200.0  # strike capital = $20,000 per contract
    sym = f"SPY{expiry:%y%m%d}P{round(strike * 1000):08d}"

    row = {
        "contract_symbol": sym,
        "option_type": "P",
        "strike": strike,
        "bid": 3.0,
        "ask": 3.2,
        "last_price": 3.1,
        "delta": -0.25,
        "implied_volatility": 0.20,
    }
    chain = options.ChainView("SPY", expiry, pd.DataFrame([row]), 205.0, True, today)
    market = FakeMarket(today=today, chain_view=chain)
    rules = PutWriteRules(put_delta=0.25)

    # Case A: Free cash $10,000 < $20,000 (1 contract cost) -> cannot afford any
    holdings_low = FakeHoldings(cash=10_000.0)
    actions_low = decide_putwrite(market, holdings_low, rules, "SPY")
    assert actions_low == [], "Cannot open put when free cash is less than 1 contract strike capital"

    # Case B: Free cash $50,000 -> int(50,000 // 20,000) = 2 contracts ($40,000 <= $50,000)
    holdings_50k = FakeHoldings(cash=50_000.0)
    actions_50k = decide_putwrite(market, holdings_50k, rules, "SPY")
    assert len(actions_50k) == 1
    act = actions_50k[0]
    assert isinstance(act, Open)
    assert act.meta["max_contracts"] == 2
    assert act.meta["max_contracts"] * strike * options.CONTRACT_MULTIPLIER <= 50_000.0

    # Case C: Free cash $19,999 -> int(19,999 // 20,000) = 0 contracts
    holdings_just_under = FakeHoldings(cash=19_999.0)
    assert decide_putwrite(market, holdings_just_under, rules, "SPY") == []

    # Case D: Free cash $20,000 exactly -> 1 contract
    holdings_exact = FakeHoldings(cash=20_000.0)
    actions_exact = decide_putwrite(market, holdings_exact, rules, "SPY")
    assert len(actions_exact) == 1
    assert actions_exact[0].meta["max_contracts"] == 1


# ------------------------------------------------------------------
# Exit Tests
# ------------------------------------------------------------------


def test_take_profit_and_exit_dte_close():
    """Positions close upon reaching take-profit (e.g. 50% credit) or exit DTE."""
    today = date(2026, 1, 15)
    rules = PutWriteRules(take_profit=0.50, exit_dte=21)

    def _held_book(pnl: float, credit: float, dte: int) -> options.OptionBook:
        from datetime import timedelta
        g = om.Greeks(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        expiry = today + timedelta(days=dte)
        sym = f"SPY{expiry:%y%m%d}P00490000"
        # value = -credit + pnl. With qty = -100, price = -(value) / 100 = (credit - pnl) / 100
        cur_price = max((credit - pnl) / 100.0, 0.0)
        pos = options.OptionPosition(
            key=sym,
            contract=options.parse_occ(sym),
            qty=-100.0,
            price=cur_price,
            iv=0.20,
            greeks=g,
        )
        return options.OptionBook(
            underlying="SPY",
            spot=500.0,
            today=today,
            positions=[pos],
            entry_value=-credit,
            greeks=g,
            shares=0.0,
        )

    market = FakeMarket(today=today, chain_view=_make_chain())

    # 1. Take profit triggered: credit $1,000, pnl $500 (50% of credit), DTE 28 > 21
    b_tp = _held_book(pnl=500.0, credit=1000.0, dte=28)
    h_tp = FakeHoldings(cash=50_000.0, book=b_tp)
    act_tp = decide_putwrite(market, h_tp, rules, "SPY")
    assert len(act_tp) == 1 and isinstance(act_tp[0], Close)
    assert "take profit" in act_tp[0].reason

    # 2. Exit DTE triggered: DTE 14 <= 21, pnl $100 < $500
    b_dte = _held_book(pnl=100.0, credit=1000.0, dte=14)
    h_dte = FakeHoldings(cash=50_000.0, book=b_dte)
    act_dte = decide_putwrite(market, h_dte, rules, "SPY")
    assert len(act_dte) == 1 and isinstance(act_dte[0], Close)
    assert "DTE" in act_dte[0].reason

    # 3. Neither triggered: DTE 25 > 21, pnl $200 < $500 -> hold (no actions)
    b_hold = _held_book(pnl=200.0, credit=1000.0, dte=25)
    h_hold = FakeHoldings(cash=50_000.0, book=b_hold)
    act_hold = decide_putwrite(market, h_hold, rules, "SPY")
    assert act_hold == [], "Must hold position when neither take-profit nor exit-DTE is reached"


def test_no_entry_when_iv_fair_gap_too_small():
    """No entry when ATM IV - HAR fair < min_gap."""
    chain = _make_chain(spot=500.0)
    holdings = FakeHoldings(cash=100_000.0)
    today = date(2026, 1, 15)

    # Flat prices -> low HAR fair, but let's test with high min_gap requirement (e.g. 50% or 0.50)
    # ATM IV in _make_chain is 0.20 (20%)
    rules_tight = PutWriteRules(min_gap=0.30)  # requires IV - fair >= 30 vol points
    market = FakeMarket(today=today, chain_view=chain)
    actions_blocked = decide_putwrite(market, holdings, rules_tight, "SPY")
    assert actions_blocked == [], "Must not enter when IV - fair gap < min_gap"

    # Relaxed gap (e.g. -0.50 or None) -> allows entry
    rules_relaxed = PutWriteRules(min_gap=-0.50)
    actions_allowed = decide_putwrite(market, holdings, rules_relaxed, "SPY")
    assert len(actions_allowed) == 1
    assert isinstance(actions_allowed[0], Open)

