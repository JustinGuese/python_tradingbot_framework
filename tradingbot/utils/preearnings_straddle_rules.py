"""
Pure decision rules for option_PreEarningsStraddleBot: buying ATM straddles
5-10 sessions before earnings reports, delta-hedging daily, and closing the
session before the report.

UNPROVEN: no backtest exists and none is claimed.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from tradingbot.utils import market_calendar, options
from tradingbot.utils.option_decide import Action, Close, Hedge, Holdings, Market, Open
from tradingbot.utils.option_rules import reaction_session

logger = logging.getLogger(__name__)

DEFAULT_ENTRY_WINDOW_MINUTES = 120


@dataclass(frozen=True)
class PreEarningsStraddleRules:
    """Configuration levers for the pre-earnings straddle run-up strategy."""

    min_sessions_before: int = 5
    max_sessions_before: int = 10
    max_front_back_ratio: float = 1.15
    max_risk_pct: float = 0.02
    max_concurrent: int = 3
    delta_band_usd: float = 2000.0
    min_dte_after_report: int = 3

    def __post_init__(self):
        if self.min_sessions_before <= 0:
            raise ValueError(f"min_sessions_before must be positive, got {self.min_sessions_before}")
        if self.max_sessions_before < self.min_sessions_before:
            raise ValueError(
                f"max_sessions_before ({self.max_sessions_before}) must be >= min_sessions_before ({self.min_sessions_before})"
            )
        if self.max_front_back_ratio <= 0:
            raise ValueError(f"max_front_back_ratio must be positive, got {self.max_front_back_ratio}")
        if self.max_risk_pct <= 0 or self.max_risk_pct > 1.0:
            raise ValueError(f"max_risk_pct must be in (0, 1], got {self.max_risk_pct}")
        if self.max_concurrent <= 0:
            raise ValueError(f"max_concurrent must be positive, got {self.max_concurrent}")
        if self.delta_band_usd < 0:
            raise ValueError(f"delta_band_usd must be non-negative, got {self.delta_band_usd}")
        if self.min_dte_after_report < 0:
            raise ValueError(f"min_dte_after_report must be non-negative, got {self.min_dte_after_report}")


def in_entry_window(now: datetime, window_minutes: int = DEFAULT_ENTRY_WINDOW_MINUTES) -> bool:
    """True if `now` is within the last `window_minutes` before regular/early session close."""
    left = market_calendar.minutes_to_close(now)
    return left is not None and 0 < left <= window_minutes


def decide_preearnings(
    market: Market,
    holdings: Holdings,
    rules: PreEarningsStraddleRules,
    universe: Sequence[str],
) -> list[Action]:
    """
    Pure decision function for PreEarningsStraddle.

    1. For every held underlying: if today is the session before the report (or later),
       return Close(u, include_stock=True); otherwise Hedge(u, band_usd).
    2. Entries: for names in universe with market.earnings_events(u) giving a report with
       known timing (after_close not None), whose reaction session is min..max_sessions_before
       sessions away (market_calendar.sessions_between), no held book, below max_concurrent:
       take market.chain(u, target_dte) with expiry at least min_dte_after_report past the
       reaction session, options.select_straddle(view), IV-ratio filter (front vs second chain),
       Open(pick, max_risk_pct * holdings.equity()).
    3. Only act inside a live market window (within 2h of close, like EarningsCrush);
       one chain failure must not stop other names (log a warning, continue).
    """
    today = market.today
    actions: list[Action] = []

    # 1. Held underlyings: close or hedge
    held = sorted(holdings.underlyings())
    closed_underlyings: set[str] = set()

    for u in held:
        opened = holdings.opened_on(u)
        events = market.earnings_events(u)

        # Find the relevant reaction session for this position
        target_rx: date | None = None
        for rep_date, after_close in events:
            if after_close is None:
                continue
            rx = reaction_session(rep_date, after_close)
            if rx is None:
                continue
            if opened is not None:
                if rx >= opened:
                    target_rx = rx
                    break
            else:
                if rx >= today:
                    target_rx = rx
                    break

        if target_rx is None and events:
            # Fallback if held over past the report
            for rep_date, after_close in reversed(events):
                if after_close is not None:
                    rx = reaction_session(rep_date, after_close)
                    if rx is not None:
                        target_rx = rx
                        break

        # Check if today is the session before the reaction session (or later)
        is_exit = False
        if target_rx is not None:
            if today >= target_rx:
                is_exit = True
            else:
                s = market_calendar.sessions_between(today, target_rx)
                if s <= 1:
                    is_exit = True

        if is_exit:
            logger.info("Closing %s pre-earnings straddle: report reaction imminent (%s)", u, target_rx)
            actions.append(Close(u, include_stock=True, reason="earnings reaction imminent or passed"))
            closed_underlyings.add(u)
        else:
            actions.append(Hedge(u, band_usd=rules.delta_band_usd))

    # 3. Only enter inside a live market window (within 2h of close, like EarningsCrush)
    if not in_entry_window(market.now):
        logger.info("PreEarningsStraddle: not in entry window (within 2h of close)")
        return actions

    active_held_count = len(set(held) - closed_underlyings)
    if active_held_count >= rules.max_concurrent:
        logger.info(
            "PreEarningsStraddle: holding %d positions (max_concurrent=%d): no entry",
            active_held_count,
            rules.max_concurrent,
        )
        return actions

    # 2. Entries
    opened_count = 0
    for u in universe:
        if u in held:
            continue
        if active_held_count + opened_count >= rules.max_concurrent:
            break

        events = market.earnings_events(u)
        # Find scheduled report with known timing
        qualifying_event: tuple[date, bool, date, int] | None = None
        for rep_date, after_close in events:
            if rep_date < today:
                continue
            if after_close is None:
                # Unknown timing: skip this name
                logger.info("%s upcoming report on %s has unknown timing", u, rep_date)
                break
            rx = reaction_session(rep_date, after_close)
            if rx is None:
                continue
            s = market_calendar.sessions_between(today, rx)
            if rules.min_sessions_before <= s <= rules.max_sessions_before:
                qualifying_event = (rep_date, after_close, rx, s)
            break  # Only consider the earliest upcoming report

        if qualifying_event is None:
            continue

        rep_date, after_close, rx, s = qualifying_event

        target_dte = (rx - today).days + rules.min_dte_after_report
        try:
            view = market.chain(u, target_dte)
        except Exception as exc:
            logger.warning("Failed loading chain for %s at target_dte=%d: %s", u, target_dte, exc)
            continue

        if view is None:
            logger.warning("No chain view returned for %s at target_dte=%d", u, target_dte)
            continue

        view_expiry = getattr(view, "expiry", getattr(view, "expiration", None))
        if view_expiry is None or (view_expiry - rx).days < rules.min_dte_after_report:
            logger.warning(
                "%s chain expiry %s is not at least %d days past reaction %s",
                u,
                view_expiry,
                rules.min_dte_after_report,
                rx,
            )
            continue

        # IV-ratio filter (front vs second chain)
        back_target_dte = (view_expiry - today).days + 1
        try:
            back_view = market.chain(u, back_target_dte)
        except Exception as exc:
            logger.warning("Failed loading back chain for %s at target_dte=%d: %s", u, back_target_dte, exc)
            back_view = None

        if back_view is not None:
            front_iv = options.atm_iv(view)
            back_iv = options.atm_iv(back_view)
            if front_iv is not None and back_iv is not None and back_iv > 0:
                ratio = front_iv / back_iv
                if ratio > rules.max_front_back_ratio:
                    logger.info(
                        "%s: front IV %.2f is %.2fx back IV %.2f > max_ratio %.2f, skipping",
                        u,
                        front_iv,
                        ratio,
                        back_iv,
                        rules.max_front_back_ratio,
                    )
                    continue

        try:
            pick = options.select_straddle(view)
        except Exception as exc:
            logger.warning("Failed selecting straddle for %s: %s", u, exc)
            continue

        budget = rules.max_risk_pct * holdings.equity()
        meta = {
            "sessions_to_reaction": s,
            "reaction_session": str(rx),
            "report_date": str(rep_date),
            "after_close": after_close,
        }
        actions.append(
            Open(
                pick,
                max_risk_usd=budget,
                reason=f"pre-earnings straddle {s} sessions before {rx}",
                meta=meta,
            )
        )
        opened_count += 1

    return actions
