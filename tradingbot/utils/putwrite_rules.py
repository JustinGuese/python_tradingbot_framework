"""
putwrite_rules.py — cash-secured put writing on SPY/QQQ with curve-slope and trend gates.

Rules:
  * Sell cash-secured short puts (cash covers strike x 100).
  * Delta: put_delta (e.g. 0.10, 0.16, 0.25, 0.30).
  * Expiry: first expiry >= target_dte (e.g. 21, 35, 45).
  * Max VIX/VIX3M gate: when set, skip entries while VIX / VIX3M > max_term_ratio (backwardation).
  * Trend filter: when set, sell puts only while close > SMA200.
  * Min IV - fair gap: when set, sell only when ATM IV >= HAR-RV fair + min_gap.
  * Exit: take-profit at share of credit (e.g. 50%), or exit at exit_dte left.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from tradingbot.utils import option_math as om
from tradingbot.utils import options
from tradingbot.utils.option_decide import Action, Close, Holdings, Market, Open
from tradingbot.utils.option_rules import short_premium_exit_reason
from tradingbot.utils.vol_indices import VIX, VIX3M, term_ratio

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PutWriteRules:
    underlying: str = "SPY"
    put_delta: float = 0.25
    target_dte: int = 35
    max_term_ratio: float | None = None
    trend_filter: bool = False
    min_gap: float | None = None
    take_profit: float | None = 0.50
    exit_dte: int | None = 21


def get_available_cash(holdings: Holdings) -> float:
    """Free cash available to secure short puts."""
    for attr in ("free_cash", "cash"):
        if hasattr(holdings, attr):
            val = getattr(holdings, attr)
            if callable(val):
                val = val()
            if isinstance(val, (int, float)) and math.isfinite(val):
                return float(val)
    if hasattr(holdings, "_book") and hasattr(holdings._book, "cash"):
        c = holdings._book.cash
        if isinstance(c, (int, float)) and math.isfinite(c):
            return float(c)
    if hasattr(holdings, "b") and hasattr(holdings.b, "cash"):
        c = holdings.b.cash
        if isinstance(c, (int, float)) and math.isfinite(c):
            return float(c)
    eq = holdings.equity()
    return float(eq) if (eq is not None and math.isfinite(eq)) else 0.0


def putwrite_entry_ok(
    view: options.ChainView | None,
    market: Market,
    rules: PutWriteRules,
    underlying: str = "SPY",
) -> bool:
    """Evaluate entry gates: live chain, VIX/VIX3M curve slope, SMA200 trend, and IV-fair gap."""
    if view is None or not view.live:
        return False

    # Gate 1: Curve slope (VIX / VIX3M term ratio)
    if rules.max_term_ratio is not None:
        vix = market.vol_index(VIX)
        vix3m = market.vol_index(VIX3M)
        term = term_ratio(vix, vix3m)
        if term is None or math.isnan(term) or term > rules.max_term_ratio:
            return False

    # Gate 2: Trend filter (close > SMA200)
    if rules.trend_filter:
        closes = market.closes(underlying)
        if closes is None or len(closes) < 200:
            return False
        sma200 = float(closes.tail(200).mean())
        if math.isnan(sma200) or float(closes.iloc[-1]) <= sma200:
            return False

    # Gate 3: Volatility risk premium gap (ATM IV - HAR fair >= min_gap)
    if rules.min_gap is not None:
        closes = market.closes(underlying)
        if closes is None or len(closes) < 2:
            return False
        iv = options.atm_iv(view)
        bdays = max(options.business_days_between(view.today, view.expiry), 1)
        fair = om.har_rv_forecast(om.log_returns(closes), bdays)
        if iv is None or fair is None or math.isnan(iv) or math.isnan(fair):
            return False
        threshold = rules.min_gap / 100.0 if rules.min_gap > 0.5 else rules.min_gap
        if (iv - fair) < threshold:
            return False

    return True


def putwrite_exit_reason(book: options.OptionBook, rules: PutWriteRules) -> str | None:
    """Exit reason based on take-profit threshold or remaining DTE."""
    return short_premium_exit_reason(book.credit, book.pnl, book.dte, rules.take_profit, rules.exit_dte)


def decide_putwrite(
    market: Market,
    holdings: Holdings,
    rules: PutWriteRules,
    underlying: str = "SPY",
) -> list[Action]:
    """Pure strategy decision for cash-secured put writing."""
    # 1. Manage open positions on the underlying
    if underlying in holdings.underlyings():
        book = holdings.book(underlying)
        if not book.empty:
            reason = putwrite_exit_reason(book, rules)
            if reason:
                logger.info("Closing %s short put: %s", underlying, reason)
                return [Close(underlying, reason=reason)]
            return []

    # 2. Check market chain
    view = market.chain(underlying, rules.target_dte)
    if view is None or not view.live:
        return []

    # 3. Check entry gates
    if not putwrite_entry_ok(view, market, rules, underlying):
        return []

    # 4. Select short put contract
    try:
        pick = options.select_short_leg(view, "P", rules.put_delta)
    except ValueError as exc:
        logger.warning("%s: no short put found near delta %.2f: %s", underlying, rules.put_delta, exc)
        return []

    if not pick.legs:
        return []

    contract_sym = pick.legs[0][0]
    strike = options.parse_occ(contract_sym).strike
    strike_capital = strike * options.CONTRACT_MULTIPLIER
    available_cash = get_available_cash(holdings)

    if strike_capital <= 0 or available_cash < strike_capital:
        logger.info(
            "%s: insufficient cash $%.2f for strike $%.2f ($%.2f required)",
            underlying,
            available_cash,
            strike,
            strike_capital,
        )
        return []

    max_contracts = int(available_cash // strike_capital)
    if max_contracts <= 0:
        return []

    # Budget worst-case loss per unit: (strike - bid) * 100
    bid = 0.0
    if view.frame is not None and not view.frame.empty:
        rows = view.frame[view.frame["contract_symbol"] == contract_sym]
        if not rows.empty:
            r = rows.iloc[0]
            if "bid" in r and pd.notna(r["bid"]) and float(r["bid"]) > 0:
                bid = float(r["bid"])
            elif "last_price" in r and pd.notna(r["last_price"]) and float(r["last_price"]) > 0:
                bid = float(r["last_price"])

    per_unit = max(strike - bid, 0.0) * options.CONTRACT_MULTIPLIER
    budget = max_contracts * per_unit

    return [
        Open(
            pick,
            budget,
            reason=f"Short put delta {rules.put_delta:.2f} strike {strike:.1f}",
            meta={"strike": strike, "bid": bid, "max_contracts": max_contracts},
        )
    ]
