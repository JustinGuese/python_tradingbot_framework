"""
Pure decision rules for option_SkewBWBBot: SPY put broken-wing butterfly (net credit, defined risk)
when 25-delta put skew is rich.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import pandas as pd

from tradingbot.utils import market_calendar, options
from tradingbot.utils.option_decide import Action, Close, Holdings, Market, Open
from tradingbot.utils.vol_indices import VIX, VIX3M, term_ratio

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SkewBWBRules:
    """Configuration levers for put broken-wing butterfly strategy."""

    underlying: str = "SPY"
    target_dte: int = 35
    min_skew: float = 0.04  # 25-delta put IV minus ATM IV
    max_term_ratio: float = 1.0  # VIX / VIX3M gate; unwind if above
    short_delta: float = 0.30
    body_width_pct: float = 0.03
    wing_width_pct: float = 0.06  # broken wing: lower wing twice the body
    max_risk_pct: float = 0.03
    take_profit: float = 0.50
    exit_dte: int = 14

    def __post_init__(self):
        if self.max_risk_pct <= 0 or self.max_risk_pct > 1.0:
            raise ValueError(f"max_risk_pct must be in (0, 1], got {self.max_risk_pct}")
        if self.body_width_pct <= 0:
            raise ValueError(f"body_width_pct must be positive, got {self.body_width_pct}")
        if self.wing_width_pct <= 0:
            raise ValueError(f"wing_width_pct must be positive, got {self.wing_width_pct}")
        if self.target_dte <= 0:
            raise ValueError(f"target_dte must be positive, got {self.target_dte}")
        if self.exit_dte < 0:
            raise ValueError(f"exit_dte must be non-negative, got {self.exit_dte}")


def put_skew(view: options.ChainView) -> float | None:
    """
    Compute 25-delta put IV minus ATM IV (in vol points as fraction, e.g. 0.04).
    Returns None on thin or invalid chains.
    """
    try:
        puts = options.with_greeks(view, "P")
        if puts is None or puts.empty:
            return None
        row_25 = options._nearest_delta(puts, -0.25)
        if row_25 is None or "iv" not in row_25 or pd.isna(row_25["iv"]):
            return None
        iv_25 = float(row_25["iv"])

        atm = options.atm_iv(view)
        if atm is None or pd.isna(atm) or math.isnan(atm):
            known = puts.dropna(subset=["iv"])
            if known.empty:
                return None
            atm_row = known.loc[(known["strike"].astype(float) - view.spot).abs().idxmin()]
            if pd.isna(atm_row["iv"]):
                return None
            atm = float(atm_row["iv"])

        if math.isnan(iv_25) or math.isnan(atm):
            return None

        return iv_25 - atm
    except Exception as exc:
        logger.warning("%s: error computing put_skew: %s", getattr(view, "underlying", ""), exc)
        return None


def select_put_bwb(view: options.ChainView, rules: SkewBWBRules) -> options.StructurePick:
    """
    Construct a put broken-wing butterfly:
      +1 put at K1 (body strike nearest short_delta)
      -2 puts at K2 = K1 - body_width
      +1 put at K3 = K2 - wing
    Legs are plain (symbol, lots). Net credit required; otherwise raises ValueError.
    """
    puts = options.with_greeks(view, "P")
    if puts is None or puts.empty:
        raise ValueError(f"No priced put contracts for {view.underlying} on {view.expiry}")

    row_1 = options._nearest_delta(puts, rules.short_delta)
    if row_1 is None:
        raise ValueError(f"No put contract found near delta {rules.short_delta}")
    k1_sym = str(row_1["contract_symbol"])
    k1 = float(row_1["strike"])

    spot = view.spot
    body_width = rules.body_width_pct * spot
    wing = rules.wing_width_pct * spot

    target_k2 = k1 - body_width
    k2_sym = options._contract_at(view, "P", target_k2)
    k2 = options.parse_occ(k2_sym).strike

    target_k3 = k2 - wing
    k3_sym = options._contract_at(view, "P", target_k3)
    k3 = options.parse_occ(k3_sym).strike

    if not (k1 > k2 > k3):
        raise ValueError(f"BWB strikes must be strictly descending: K1={k1}, K2={k2}, K3={k3}")

    priced = options.priced_side(view, "P").set_index("contract_symbol")
    p1 = float(priced.loc[k1_sym, "price"])
    p2 = float(priced.loc[k2_sym, "price"])
    p3 = float(priced.loc[k3_sym, "price"])

    # Net credit received per share: selling 2 puts at K2, buying 1 at K1 and 1 at K3
    net_credit = 2 * p2 - p1 - p3
    if net_credit <= 0:
        raise ValueError(f"Put broken-wing butterfly is not a net credit: net credit {net_credit:.4f} <= 0")

    legs = [(k1_sym, 1), (k2_sym, -2), (k3_sym, 1)]
    return options._pick(view, legs)


def decide_skewbwb(
    market: Market,
    holdings: Holdings,
    rules: SkewBWBRules,
    underlying: str = "SPY",
) -> list[Action]:
    """
    Pure decision function for OptionSkewBWBBot.
    - Exits on take_profit (pnl_pct >= rules.take_profit), DTE <= rules.exit_dte,
      or term structure inverted (VIX / VIX3M > rules.max_term_ratio).
    - Enters when flat, NYSE session active, live chain, put_skew >= rules.min_skew,
      and term structure gate passes (VIX / VIX3M <= rules.max_term_ratio).
    """
    held = underlying in holdings.underlyings()
    if held:
        book = holdings.book(underlying)
        if not book.empty:
            # 1. Take profit
            if rules.take_profit is not None and book.pnl_pct >= rules.take_profit:
                return [
                    Close(
                        underlying,
                        include_stock=True,
                        reason=f"take profit: {book.pnl_pct:.1%} >= {rules.take_profit:.0%}",
                    )
                ]

            # 2. DTE exit
            if rules.exit_dte is not None and book.dte is not None and book.dte <= rules.exit_dte:
                return [Close(underlying, include_stock=True, reason=f"DTE {book.dte} <= {rules.exit_dte}")]

            # 3. Term structure inverted (unwind)
            vix = market.vol_index(VIX)
            vix3m = market.vol_index(VIX3M)
            term = term_ratio(vix, vix3m)
            if term is not None and term > rules.max_term_ratio:
                return [
                    Close(
                        underlying,
                        include_stock=True,
                        reason=f"term ratio {term:.2f} > {rules.max_term_ratio:.2f} (inverted)",
                    )
                ]

            # Still held, no exit condition triggered -> do not enter again
            return []

    # Entry evaluation
    if not market_calendar.is_session(market.today):
        return []

    view = market.chain(underlying, rules.target_dte)
    if view is None or not view.live:
        return []

    # Check term structure gate
    if rules.max_term_ratio is not None:
        vix = market.vol_index(VIX)
        vix3m = market.vol_index(VIX3M)
        term = term_ratio(vix, vix3m)
        if term is None or math.isnan(term) or term > rules.max_term_ratio:
            return []

    # Check skew
    skew = put_skew(view)
    if skew is None or skew < rules.min_skew:
        return []

    # Structure selection
    try:
        pick = select_put_bwb(view, rules)
    except ValueError as exc:
        logger.warning("%s: failed to select put BWB: %s", underlying, exc)
        return []

    equity = holdings.equity()
    max_risk_usd = rules.max_risk_pct * equity
    if max_risk_usd <= 0:
        return []

    return [Open(pick, max_risk_usd=max_risk_usd, reason=f"put skew {skew:.3f} >= {rules.min_skew:.3f}")]
