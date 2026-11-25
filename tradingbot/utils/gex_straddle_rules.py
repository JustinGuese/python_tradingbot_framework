"""
Pure decision rules for option_GexStraddleBot: SPY 0DTE straddle on negative-gamma days.

Dealer GEX < 0 and VIX/VIX3M > 1 (backwardation), 2x take-profit, flat by 15:45 NY.
Long premium, defined risk; never delta-hedged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import time
from zoneinfo import ZoneInfo

import pandas as pd

from tradingbot.utils import market_calendar, options, vol_surface
from tradingbot.utils import option_math as om
from tradingbot.utils.option_decide import Action, Close, Holdings, Market, Open
from tradingbot.utils.vol_indices import VIX, VIX3M, term_ratio

logger = logging.getLogger(__name__)

NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class GexStraddleRules:
    """Configuration levers for 0DTE GEX straddle strategy."""

    underlying: str = "SPY"
    entry_not_before_ny: time = time(10, 0)
    entry_not_after_ny: time = time(11, 0)
    max_term_ratio_min: float = 1.0  # needs VIX/VIX3M > 1
    gex_threshold: float = 0.0  # needs GEX < this
    take_profit: float = 1.0  # +100% = 2x
    flat_time_ny: time = time(15, 45)
    max_risk_pct: float = 0.005  # 0.5% of book per ticket

    def __post_init__(self):
        if self.max_risk_pct <= 0 or self.max_risk_pct > 1.0:
            raise ValueError(f"max_risk_pct must be in (0, 1], got {self.max_risk_pct}")
        if self.take_profit <= 0:
            raise ValueError(f"take_profit must be positive, got {self.take_profit}")
        if self.entry_not_before_ny >= self.entry_not_after_ny:
            raise ValueError(
                f"entry_not_before_ny ({self.entry_not_before_ny}) must be before entry_not_after_ny ({self.entry_not_after_ny})"
            )
        if self.entry_not_after_ny > self.flat_time_ny:
            raise ValueError(
                f"entry_not_after_ny ({self.entry_not_after_ny}) cannot be after flat_time_ny ({self.flat_time_ny})"
            )


def chain_gex(view: options.ChainView) -> float | None:
    """Compute dealer GEX from a live 0DTE chain via options.with_greeks for C and P.

    Concatenated into a frame with option_type, gamma, open_interest, then
    evaluated via vol_surface.gamma_exposure.
    Returns None when open interest is missing or all zero.
    """
    if view is None or view.frame is None or view.frame.empty:
        return None

    if "open_interest" not in view.frame.columns:
        return None

    oi_series = pd.to_numeric(view.frame["open_interest"], errors="coerce").fillna(0.0)
    if oi_series.sum() <= 0:
        return None

    try:
        calls = options.with_greeks(view, "C")
    except Exception as exc:
        logger.warning("%s: failed to get call greeks: %s", view.underlying, exc)
        calls = pd.DataFrame()

    try:
        puts = options.with_greeks(view, "P")
    except Exception as exc:
        logger.warning("%s: failed to get put greeks: %s", view.underlying, exc)
        puts = pd.DataFrame()

    if calls.empty and puts.empty:
        return None

    combined = pd.concat([calls, puts], ignore_index=True)
    if combined.empty:
        return None

    if "open_interest" not in combined.columns:
        return None

    comb_oi = pd.to_numeric(combined["open_interest"], errors="coerce").fillna(0.0)
    if comb_oi.sum() <= 0:
        return None

    # Ensure gamma column is present and valid
    if "gamma" not in combined.columns or combined["gamma"].isna().all():
        r = options.risk_free_rate()
        q = options.dividend_yield(view.underlying)
        T = view.T if view.T > 0 else 0.5 / 365.0
        gammas = []
        for _, row in combined.iterrows():
            k = float(row["strike"])
            iv = row.get("iv")
            if pd.isna(iv) or iv is None or iv <= 0:
                iv = row.get("implied_volatility")
            if pd.isna(iv) or iv is None or iv <= 0:
                iv = 0.20
            gammas.append(om.gamma(view.spot, k, T, r, float(iv), q))
        combined["gamma"] = gammas
    elif combined["gamma"].isna().any():
        r = options.risk_free_rate()
        q = options.dividend_yield(view.underlying)
        T = view.T if view.T > 0 else 0.5 / 365.0
        gammas = []
        for _, row in combined.iterrows():
            g = row["gamma"]
            if pd.isna(g):
                k = float(row["strike"])
                iv = row.get("iv")
                if pd.isna(iv) or iv is None or iv <= 0:
                    iv = row.get("implied_volatility", 0.20)
                g = om.gamma(view.spot, k, T, r, float(iv) if pd.notna(iv) and float(iv) > 0 else 0.20, q)
            gammas.append(float(g))
        combined["gamma"] = gammas

    return vol_surface.gamma_exposure(combined, view.spot)


def decide_gexstraddle(
    market: Market,
    holdings: Holdings,
    rules: GexStraddleRules,
    underlying: str = "SPY",
    entered_today: bool = False,
) -> list[Action]:
    """Pure decision function for OptionGexStraddleBot.

    `entered_today` must come from the trade log: once flat, `holdings.opened_on` is None, so it
    cannot tell that a ticket was already opened and closed (take-profit) earlier this session.

    - Exits open position if:
        * pnl_pct >= rules.take_profit (take profit, default +100%)
        * now_ny >= rules.flat_time_ny (force-close intraday, default 15:45 NY)
        * market.today > opened_on (force-close holdover from a prior session)
    - Enters 0DTE straddle once per session inside the entry window (10:00 - 11:00 NY):
        * when flat and no entry earlier today (holdings.opened_on != market.today)
        * today is an active NYSE session
        * market.chain(u, 0) is live with expiry == market.today
        * dealer GEX < rules.gex_threshold (negative dealer gamma regime)
        * VIX / VIX3M > rules.max_term_ratio_min (backwardation / term structure inversion)
    - Never hedges (long premium, defined risk).
    """
    underlying = underlying or rules.underlying
    held = underlying in holdings.underlyings()

    if held:
        opened = holdings.opened_on(underlying)
        # Force-close holdover from previous session
        if opened is not None and market.today > opened:
            logger.info("%s: force-closing holdover position opened on %s", underlying, opened)
            return [Close(underlying, include_stock=True, reason="Holdover from prior session")]

        book = holdings.book(underlying)
        if book.pnl_pct >= rules.take_profit:
            logger.info("%s: take profit triggered (pnl=%.1f%%)", underlying, book.pnl_pct * 100)
            return [Close(underlying, include_stock=True, reason=f"Take profit {book.pnl_pct:.0%}")]

        now_ny = market.now.astimezone(NEW_YORK) if market.now.tzinfo else market.now.replace(tzinfo=NEW_YORK)
        if now_ny.time() >= rules.flat_time_ny:
            logger.info("%s: flat time reached (%s >= %s)", underlying, now_ny.time(), rules.flat_time_ny)
            return [Close(underlying, include_stock=True, reason="Flat by 15:45 NY")]

        return []

    # Entry evaluation
    if not market_calendar.is_session(market.today):
        logger.info("%s: today (%s) is not an active NYSE session", underlying, market.today)
        return []

    opened = holdings.opened_on(underlying)
    if opened == market.today or entered_today:
        logger.info("%s: already entered earlier today (%s); no re-entry", underlying, market.today)
        return []

    now_ny = market.now.astimezone(NEW_YORK) if market.now.tzinfo else market.now.replace(tzinfo=NEW_YORK)
    cur_time = now_ny.time()
    if not (rules.entry_not_before_ny <= cur_time <= rules.entry_not_after_ny):
        logger.info(
            "%s: current time %s outside entry window [%s, %s]",
            underlying,
            cur_time,
            rules.entry_not_before_ny,
            rules.entry_not_after_ny,
        )
        return []

    view = market.chain(underlying, 0)
    if view is None or not view.live:
        logger.info("%s: chain not live or unavailable", underlying)
        return []

    if view.expiry != market.today:
        logger.info("%s: chain expiry %s is not 0DTE (today=%s)", underlying, view.expiry, market.today)
        return []

    gex = chain_gex(view)
    if gex is None or gex >= rules.gex_threshold:
        logger.info("%s: GEX %s not below threshold %s", underlying, gex, rules.gex_threshold)
        return []

    vix = market.vol_index(VIX)
    vix3m = market.vol_index(VIX3M)
    ratio = term_ratio(vix, vix3m)
    if ratio is None or ratio <= rules.max_term_ratio_min:
        logger.info(
            "%s: term ratio %s not above min %s (VIX=%s, VIX3M=%s)",
            underlying,
            ratio,
            rules.max_term_ratio_min,
            vix,
            vix3m,
        )
        return []

    try:
        pick = options.select_straddle(view)
    except Exception as exc:
        logger.warning("%s: failed to select straddle: %s", underlying, exc)
        return []

    budget = max(rules.max_risk_pct * holdings.equity(), 0.0)
    meta = {
        "strategy": "GexStraddle",
        "underlying": underlying,
        "entry_date": str(market.today),
        "gex": gex,
        "term_ratio": ratio,
        "vix": vix,
        "vix3m": vix3m,
    }
    return [
        Open(
            pick=pick,
            max_risk_usd=budget,
            reason=f"GEX straddle entry: GEX={gex:.0f}, VIX/VIX3M={ratio:.2f}",
            meta=meta,
        )
    ]
