"""
Pure decision rules for option_FOMCVolBot: trading index event vol into FOMC statements.

FOMC statements are scheduled macro events (dates in utils/macro_calendar).
The strategy enters at the close of the session immediately prior to the FOMC
statement (session T-1) and exits after the 14:00 New York announcement on the
statement day (session T0).

Supports defined-risk structures (iron fly) as well as straddles and strangles,
long or short, unhedged or delta-hedged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from tradingbot.utils import macro_calendar, market_calendar, options
from tradingbot.utils.option_decide import Action, Close, Hedge, Holdings, Market, Open

logger = logging.getLogger(__name__)

NEW_YORK = ZoneInfo("America/New_York")
DEFAULT_FOMC_ANNOUNCEMENT_TIME = time(14, 0)
DEFAULT_ENTRY_WINDOW_MINUTES = 120


@dataclass(frozen=True)
class FOMCVolRules:
    """Configuration levers for FOMC event vol strategy."""

    side: str = "long"  # "long" or "short"
    structure: str = "iron_fly"  # "iron_fly", "straddle", or "strangle"
    wing_width_pct: float = 0.05  # wing width as fraction of spot for iron fly
    strike_offset: float = 0.0  # strike offset from ATM in dollars
    target_dte: int = 1  # minimum DTE to target front expiry spanning FOMC
    max_risk_pct: float = 0.05  # max risk budget as fraction of equity
    delta_hedged: bool = False
    entry_window_minutes: int = DEFAULT_ENTRY_WINDOW_MINUTES
    exit_time_ny: time = DEFAULT_FOMC_ANNOUNCEMENT_TIME

    def __post_init__(self):
        if self.side not in ("short", "long"):
            raise ValueError(f"side must be 'short' or 'long', got {self.side!r}")
        if self.structure not in ("iron_fly", "straddle", "strangle"):
            raise ValueError(f"structure must be 'iron_fly', 'straddle', or 'strangle', got {self.structure!r}")
        if self.wing_width_pct <= 0:
            raise ValueError(f"wing_width_pct must be positive, got {self.wing_width_pct}")
        if self.max_risk_pct <= 0 or self.max_risk_pct > 1.0:
            raise ValueError(f"max_risk_pct must be in (0, 1], got {self.max_risk_pct}")


def next_fomc_date(today: date, dates: tuple[date, ...] = macro_calendar.FOMC_STATEMENT_DATES) -> date | None:
    """The earliest FOMC statement date on or after `today`, or None if past calendar end."""
    for d in dates:
        if d >= today:
            return d
    return None


def is_fomc_entry_session(today: date, dates: tuple[date, ...] = macro_calendar.FOMC_STATEMENT_DATES) -> bool:
    """True if `today` is the NYSE trading session immediately prior to an FOMC statement date."""
    if not market_calendar.is_session(today):
        return False
    nxt = next_fomc_date(today, dates)
    if nxt is None:
        return False
    return market_calendar.sessions_between(today, nxt) == 1


def in_entry_window(now: datetime, window_minutes: int = DEFAULT_ENTRY_WINDOW_MINUTES) -> bool:
    """True if `now` is within the last `window_minutes` before regular/early session close."""
    left = market_calendar.minutes_to_close(now)
    return left is not None and 0 < left <= window_minutes


def is_fomc_exit_session(
    today: date,
    opened_on: date | None = None,
    now: datetime | None = None,
    dates: tuple[date, ...] = macro_calendar.FOMC_STATEMENT_DATES,
    exit_time_ny: time = DEFAULT_FOMC_ANNOUNCEMENT_TIME,
) -> bool:
    """
    True if the FOMC announcement for the trade has already occurred.

    Exits on the first run after the announcement:
      - On announcement session T0: after exit_time_ny (14:00 New York).
      - On any session T > T0: always True.
    """
    if opened_on is not None:
        target_fomc = next_fomc_date(opened_on, dates)
    else:
        past = [d for d in dates if d <= today]
        target_fomc = past[-1] if past else None

    if target_fomc is None:
        return False

    if today > target_fomc:
        return True

    if today == target_fomc:
        if now is None:
            return True
        now_ny = now.astimezone(NEW_YORK) if now.tzinfo else now.replace(tzinfo=NEW_YORK)
        return now_ny.time() >= exit_time_ny

    return False


def select_fomc_structure(view: options.ChainView, rules: FOMCVolRules) -> options.StructurePick:
    """Select the option contracts and legs for the configured FOMC structure."""
    spot = view.spot
    k_atm = options._atm_strike(view)
    k_center = k_atm + rules.strike_offset

    if rules.structure == "straddle":
        call_sym = options._contract_at(view, "C", k_center)
        put_sym = options._contract_at(view, "P", k_center)
        mult = 1 if rules.side == "long" else -1
        legs = [(call_sym, mult), (put_sym, mult)]
        return options._pick(view, legs)

    elif rules.structure == "iron_fly":
        width = max(round(rules.wing_width_pct * spot), 1.0)
        call_atm = options._contract_at(view, "C", k_center)
        put_atm = options._contract_at(view, "P", k_center)
        put_wing = options._contract_at(view, "P", k_center - width)
        call_wing = options._contract_at(view, "C", k_center + width)

        if rules.side == "short":
            # Short iron fly: sell ATM straddle, buy OTM wings (defined-risk short vol)
            legs = [(put_wing, 1), (put_atm, -1), (call_atm, -1), (call_wing, 1)]
        else:
            # Long iron fly: buy ATM straddle, sell OTM wings (defined-risk long vol)
            legs = [(put_wing, -1), (put_atm, 1), (call_atm, 1), (call_wing, -1)]
        return options._pick(view, legs)

    elif rules.structure == "strangle":
        # 10-delta / ~3% OTM strangle
        kp = None
        kc = None
        try:
            puts = options.with_greeks(view, "P")
            row_p = options._nearest_delta(puts, -0.10)
            if row_p is not None:
                kp = str(row_p["contract_symbol"])
            calls = options.with_greeks(view, "C")
            row_c = options._nearest_delta(calls, 0.10)
            if row_c is not None:
                kc = str(row_c["contract_symbol"])
        except Exception:
            pass
        if kp is None:
            kp = options._contract_at(view, "P", round(spot * (1.0 - rules.wing_width_pct) + rules.strike_offset))
        if kc is None:
            kc = options._contract_at(view, "C", round(spot * (1.0 + rules.wing_width_pct) + rules.strike_offset))
        mult = 1 if rules.side == "long" else -1
        legs = [(kp, mult), (kc, mult)]
        return options._pick(view, legs)

    raise ValueError(f"Unsupported structure: {rules.structure}")


def decide_fomcvol(market: Market, holdings: Holdings, rules: FOMCVolRules, underlying: str = "SPY") -> list[Action]:
    """
    Pure decision function for FOMCVolBot.

    - Exits open position if announcement session has passed or announcement occurred.
    - Enters exactly one session before FOMC within the entry window.
    - Refuses off-hours runs or unlive chains.
    - Caps worst-case risk to rules.max_risk_pct of portfolio equity.
    """
    held = underlying in holdings.underlyings()

    if held:
        opened = holdings.opened_on(underlying)
        if is_fomc_exit_session(market.today, opened, market.now, exit_time_ny=rules.exit_time_ny):
            logger.info("%s: exiting FOMC position after announcement", underlying)
            return [Close(underlying, include_stock=True, reason="FOMC announcement passed")]
        if rules.delta_hedged:
            return [Hedge(underlying, band_usd=0.0)]
        return []

    # Entry evaluation
    if not market_calendar.is_session(market.today):
        logger.info("%s: today is not an active NYSE session", market.today)
        return []

    if not in_entry_window(market.now, rules.entry_window_minutes):
        logger.info("Not in entry window (%d min before close)", rules.entry_window_minutes)
        return []

    if not is_fomc_entry_session(market.today):
        return []

    view = market.chain(underlying, rules.target_dte)
    if view is None or not view.live:
        logger.info("%s: chain not live or unavailable; not opening off-hours", underlying)
        return []

    try:
        pick = select_fomc_structure(view, rules)
    except Exception as exc:
        logger.warning("%s: failed to build %s %s structure: %s", underlying, rules.structure, rules.side, exc)
        return []

    budget = max(rules.max_risk_pct * holdings.equity(), 0.0)
    meta = {
        "strategy": "FOMCVol",
        "underlying": underlying,
        "entry_date": str(market.today),
        "side": rules.side,
        "structure": rules.structure,
        "wing_width_pct": rules.wing_width_pct,
        "strike_offset": rules.strike_offset,
    }
    action = Open(
        pick=pick,
        max_risk_usd=budget,
        reason=f"FOMC entry: {rules.side} {rules.structure}",
        meta=meta,
    )
    actions: list[Action] = [action]
    if rules.delta_hedged:
        actions.append(Hedge(underlying, band_usd=0.0))
    return actions
