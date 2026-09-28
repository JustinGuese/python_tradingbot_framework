"""
option_EarningsCrushBot — sell the earnings move when the options price it
well above the stock's own history, across the 50 captured names.

Into a report, the nearest expiry carries the whole expected earnings jump in
its implied vol, and the day after, that vol collapses. On average large caps
price the move somewhat above what they deliver; this sells it only when the
premium is large, with defined risk, and holds it over the report only.

Rules (utils/option_rules.EarningsCrushRules):
  * Timing from each report's time stamp: before-the-open reports react the
    same session, after-the-close ones the next (options.earnings_events).
    Unknown timing is skipped.
  * Enter on the 19:30 UTC run of the session right before the reaction.
  * Implied move from two expiries: the first expiring on/after the reaction
    session and the next one at least 5 days later (om.implied_earnings_move),
    so the base diffusion vol is backed out rather than guessed.
  * Historical move: RMS of the last 12 reaction-session returns.
  * Sell only if implied / historical >= min_ratio: an iron butterfly at the
    money, wings wing_moves implied moves away, worst-case loss
    <= risk_per_trade_pct of the book; at most max_concurrent at once.
  * Close on the first run after the reaction session opens (the 14:30 UTC run).

Evidence: none yet. Historical earnings IV cannot be priced without per-name
option history; paper, unproven, live-only. The scan's "event" rows log the
same ratio for every reporting name, which is the record to judge it against.

Schedule: 30 14,19 * * 1-5 (14:30 UTC exits after the open, 19:30 UTC enters
before the close; both inside US hours in summer and winter).
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import ClassVar

from tradingbot.utils import mispricing_scan as ms
from tradingbot.utils import option_math as om
from tradingbot.utils import options
from tradingbot.utils.botclass import Bot
from tradingbot.utils.option_rules import (
    EarningsCrushRules,
    business_days,
    earnings_crush_entry_ok,
    earnings_crush_exit_due,
    reaction_session,
)
from tradingbot.utils.runner import run_bot
from tradingbot.utils.universes import OPTION_CAPTURE_UNIVERSE

logger = logging.getLogger(__name__)

UNIVERSE = OPTION_CAPTURE_UNIVERSE[2:]
ENTRY_HOUR_UTC = 17  # runs at or after this hour may enter; earlier runs only exit


class OptionEarningsCrushBot(Bot):
    INITIAL_CAPITAL: ClassVar[float] = 100_000.0
    OPTION_ROLL_DTE: ClassVar[int | None] = None
    RULES: ClassVar[EarningsCrushRules] = EarningsCrushRules()

    def __init__(self, **kwargs):
        super().__init__("option_EarningsCrushBot", symbol="SPY", interval="1d", period="1y", **kwargs)

    def makeOneIteration(self, now: datetime | None = None) -> int:
        rules = self.RULES
        now = now or datetime.now(UTC)
        today = options.utc_today()
        closed = self._manage(today)
        if now.hour < ENTRY_HOUR_UTC:
            return -1 if closed else 0
        held = options.option_underlyings(self.dbBot.portfolio)
        if len(held) >= rules.max_concurrent:
            logger.info("Holding %d earnings trades: no free slot", len(held))
            return -1 if closed else 0

        reporting = []
        for u in UNIVERSE:
            if u in held:
                continue
            event = options.next_earnings_event(u, today)
            if event is None:
                continue
            reaction = reaction_session(*event)
            if reaction is not None and business_days(today, reaction) == 1:
                reporting.append((u, event, reaction))
        if not reporting:
            logger.info("No name reacts to earnings tomorrow")
            return -1 if closed else 0
        ohlc = ms.load_ohlc([u for u, _, _ in reporting])

        opened = 0
        for u, (report, after_close), reaction in reporting:
            if len(held) + opened >= rules.max_concurrent:
                break
            try:
                opened += self._maybe_enter(u, report, after_close, reaction, ohlc.get(u), today)
            except Exception as exc:
                logger.warning("%s: skipped (%s)", u, exc)
        return 1 if opened else (-1 if closed else 0)

    def _maybe_enter(self, u, report, after_close, reaction, ohlc, today) -> int:
        rules = self.RULES
        front = options.load_chain(u, (reaction - today).days, today=today)
        if not front.live:
            logger.info("%s: chain not live", u)
            return 0
        if (front.expiry - reaction).days > rules.front_max_days_after:
            logger.info("%s: first expiry %s too long after the report", u, front.expiry)
            return 0
        back = options.load_chain(u, (front.expiry - today).days + rules.back_min_days_after_front, today=today)
        front_iv, back_iv = options.atm_iv(front), options.atm_iv(back)
        implied = om.implied_earnings_move(front_iv, front.T, back_iv, back.T) if front_iv and back_iv else None
        hist, n = None, 0
        if ohlc is not None:
            events = [e for e in options.earnings_events(u) if e[0] < today]
            reactions = om.earnings_reaction_returns(ohlc["close"], [d for d, _ in events], dict(events))
            recent = reactions.tail(rules.hist_events)
            n, hist = len(recent), (om.earnings_jump(recent.tolist()) if len(recent) else None)
        ok, why = earnings_crush_entry_ok(today, reaction, implied, hist, n, rules)
        logger.info("%s reports %s (%s): %s", u, report, "after close" if after_close else "before open", why)
        if not ok:
            return 0
        width = rules.wing_moves * implied * front.spot
        pick = options.select_iron_butterfly(front, width)
        units = self.open_structure(pick, rules.risk_per_trade_pct * self.portfolio_value())
        if units:
            logger.info("Sold %d iron butterflies on %s, wings %.2f away", units, u, width)
        return 1 if units else 0

    def _manage(self, today) -> int:
        """Close every structure opened before today: entries happen only on the
        session right before the reaction, so by the next run it has happened."""
        closed = 0
        for u in sorted(options.option_underlyings(self.dbBot.portfolio)):
            legs = options.option_legs(self.dbBot.portfolio, u)
            opened = min((d for d in (options.opened_on(self.bot_name, k) for k in legs) if d), default=None)
            if earnings_crush_exit_due(today, None if opened is None else opened + timedelta(days=1)):
                book = self.option_book(u)
                logger.info("Closing %s after the report: P&L %.2f on credit %.2f", u, book.pnl, book.credit)
                self.close_options(u)
                closed += 1
        return closed


if __name__ == "__main__":
    run_bot(OptionEarningsCrushBot)
