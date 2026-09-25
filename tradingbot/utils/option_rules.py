"""
Entry, exit and sizing rules of the option_* bots, as pure functions.

Kept out of the bot classes so the synthetic backtest
(scripts/onetime_option_bots_backtest.py) runs exactly the rules the live bots
run; only the prices differ (live chain vs Black-Scholes on an IV proxy).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def sma(close: pd.Series, window: int) -> float:
    tail = close.dropna().iloc[-window:]
    return float(tail.mean()) if len(tail) >= window else float("nan")


def trend_side(close: float, sma50: float, sma200: float) -> str | None:
    """Return "bull" when close > SMA50 > SMA200, "bear" when stacked the other way, else None."""
    if any(np.isnan(x) for x in (close, sma50, sma200)):
        return None
    if close > sma50 > sma200:
        return "bull"
    if close < sma50 < sma200:
        return "bear"
    return None


def business_days(start: date, end: date) -> int:
    """Weekdays from start (inclusive) to end (exclusive); negative when end < start."""
    return int(np.busday_count(start, end))


def earnings_clear(earnings: date | None, expiry: date, today: date) -> bool:
    """
    True when no scheduled earnings fall between today and expiry. An unknown
    date counts as clear (logged): blocking on a flaky calendar would stop the
    bot for good, and AAPL's dates are almost always published.
    """
    if earnings is None:
        logger.warning("Earnings date unknown; treating %s..%s as clear", today, expiry)
        return True
    return not today <= earnings <= expiry


# ------------------------------------------------------------------
# Premium selling: credit spreads and iron condors
# ------------------------------------------------------------------


@dataclass(frozen=True)
class CreditRules:
    short_delta: float = 0.30
    width: float = 10.0
    target_dte: int = 35  # first expiry at least this far out: ~35-42 days
    min_iv_hv: float = 1.10  # course: sell when implied vol is well above historical
    hv_window: int = 20
    max_vix: float = 35.0  # no new short premium into a panic
    max_adx: float | None = None  # iron condor: only in a range-bound tape
    max_risk_pct: float = 0.25  # worst-case loss per position, share of the book
    take_profit: float = 0.50  # close at this share of the credit
    stop_loss: float = 2.0  # close when the loss reaches this multiple of the credit
    exit_dte: int = 21  # close with this few days left: gamma risk outgrows the theta left
    # "trend": bull put spread in an uptrend, bear call spread in a downtrend.
    # "bull": bull put spreads only (in any tape that is not a downtrend) — the
    # side that collects the equity drift and the put skew instead of fighting them.
    sides: str = "trend"
    call_delta: float | None = None  # iron condor call side; None = short_delta


def credit_side(close: float, sma50: float, sma200: float, rules: CreditRules) -> str | None:
    """Which vertical to sell today: "bull", "bear", or None."""
    side = trend_side(close, sma50, sma200)
    if rules.sides == "bull":
        return None if side == "bear" or np.isnan(sma200) else "bull"
    return side


def premium_selling_ok(iv_hv: float | None, vix: float | None, adx: float | None, rules: CreditRules) -> bool:
    if iv_hv is None or iv_hv < rules.min_iv_hv:
        return False
    if vix is not None and vix >= rules.max_vix:
        return False
    return not (rules.max_adx is not None and (adx is None or adx >= rules.max_adx))


def credit_exit_reason(credit: float, pnl: float, dte: int | None, rules: CreditRules) -> str | None:
    """Why to close a credit position now, or None to keep holding."""
    if dte is not None and dte <= rules.exit_dte:
        return f"{dte} DTE <= {rules.exit_dte}"
    if credit <= 0:
        return None
    if pnl >= rules.take_profit * credit:
        return f"take profit: {pnl:.2f} >= {rules.take_profit:.0%} of credit {credit:.2f}"
    if -pnl >= rules.stop_loss * credit:
        return f"stop loss: {pnl:.2f} <= -{rules.stop_loss:g}x credit {credit:.2f}"
    return None


# ------------------------------------------------------------------
# LEAP call: stock replacement
# ------------------------------------------------------------------


@dataclass(frozen=True)
class LeapRules:
    delta: float = 0.70
    target_dte: int = 540  # 1.5y: inside the course's 1-2 year LEAP window
    roll_dte: int = 180  # roll with ~6 months left, before theta accelerates
    max_iv_hv: float = 1.20  # course: buy options when IV is not rich
    hv_window: int = 60
    exit_buffer: float = 0.03  # exit below SMA200 x (1 - buffer), to avoid whipsaw
    leverage: float = 1.0  # delta-dollars per dollar of book
    # Trim back to `leverage` once delta-dollars exceed this multiple of the book.
    # None never trims: gains then compound the exposure (to ~1.6x on average).
    max_leverage: float | None = None


def leap_contracts(book_value: float, spot: float, delta: float, leverage: float) -> int:
    """Whole contracts whose delta-dollars come closest to leverage x book."""
    per_contract = delta * spot * 100
    return max(0, round(book_value * leverage / per_contract)) if per_contract > 0 else 0


def leap_trim_contracts(
    delta_dollars: float, book_value: float, delta_dollars_per_contract: float, rules: LeapRules
) -> int:
    """Contracts to sell so exposure returns to rules.leverage x book (0 if within max_leverage)."""
    if rules.max_leverage is None or book_value <= 0 or delta_dollars_per_contract <= 0:
        return 0
    if delta_dollars <= rules.max_leverage * book_value:
        return 0
    return math.ceil((delta_dollars - rules.leverage * book_value) / delta_dollars_per_contract)


def leap_signal(close: float, sma200: float, rules: LeapRules) -> int:
    """1 = may hold/enter, -1 = must exit, 0 = in the buffer band (hold what you have, no entry)."""
    if np.isnan(sma200):
        return 0
    if close > sma200:
        return 1
    if close < sma200 * (1 - rules.exit_buffer):
        return -1
    return 0


# ------------------------------------------------------------------
# Catalyst call: long call into earnings, sold before the announcement
# ------------------------------------------------------------------


@dataclass(frozen=True)
class CatalystRules:
    delta: float = 0.75  # course: ITM call, delta .75, when bullish
    entry_min_bdays: int = 10  # enter 10-25 trading days before earnings,
    entry_max_bdays: int = 25  # before the pre-earnings IV run-up is priced in
    days_after_earnings: int = 14  # expiry at least this long after earnings
    max_iv_hv: float = 1.0  # course: buy when implied vol is low
    hv_window: int = 20
    trend_window: int = 50
    position_pct: float = 0.30
    take_profit: float = 0.50
    stop_loss: float = 0.40


def catalyst_entry_ok(today: date, earnings: date | None, rules: CatalystRules) -> bool:
    if earnings is None:
        return False
    return rules.entry_min_bdays <= business_days(today, earnings) <= rules.entry_max_bdays


def catalyst_exit_reason(
    today: date, earnings: date | None, expiry: date, pnl_pct: float, rules: CatalystRules
) -> str | None:
    """
    Sell the day before earnings: keep the run-up in implied vol, skip the
    crush. If the next known earnings is already past this expiry, the catalyst
    we bought for has been missed (the bot did not run in time), so exit too.
    """
    if pnl_pct >= rules.take_profit:
        return f"take profit {pnl_pct:.0%}"
    if pnl_pct <= -rules.stop_loss:
        return f"stop loss {pnl_pct:.0%}"
    if earnings is not None and (business_days(today, earnings) <= 1 or earnings > expiry):
        return f"earnings {earnings}: exit before the announcement"
    return None
