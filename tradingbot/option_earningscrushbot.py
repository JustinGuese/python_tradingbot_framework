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
  * Enter on the last run of the session right before the reaction: any run
    within 2 hours of that session's close (market_calendar), so 19:30 UTC on
    a normal day and 16:30 UTC on an early close (13:00 New York).
  * Implied move from two expiries: the first expiring on/after the reaction
    session and the next one at least 5 days later (om.implied_earnings_move),
    so the base diffusion vol is backed out rather than guessed.
  * Historical move: RMS of the last 12 reaction-session returns.
  * Sell only if implied / historical >= min_ratio: an iron butterfly at the
    money, wings wing_moves implied moves away, worst-case loss
    <= risk_per_trade_pct of the book; at most max_concurrent at once.
  * Close on the first run after the reaction session opens (the 14:30 UTC run).
  * Before-open vs after-close timing comes from the stored stock_earnings
    rows (corporateeventssnapshot), and the reaction session skips holidays.

Evidence: none yet. Historical earnings IV cannot be priced without per-name
option history; paper, unproven, live-only. The scan's "event" rows log the
same ratio for every reporting name, which is the record to judge it against.

Schedule: 30 14,16,19 * * 1-5. 14:30 UTC exits after the open. 19:30 UTC
enters before a normal close, in summer and winter. 16:30 UTC enters only on
an early-close day; the entry window turns it into an exit-only run otherwise.
"""

import logging
from typing import ClassVar

from tradingbot.utils.option_rules import EarningsCrushRules
from tradingbot.utils.option_strategies import ENTRY_WINDOW_MINUTES, decide_earningscrush, in_entry_window
from tradingbot.utils.option_strategy_bot import OptionUniverseBot
from tradingbot.utils.runner import run_bot
from tradingbot.utils.universes import OPTION_CAPTURE_UNIVERSE

logger = logging.getLogger(__name__)

UNIVERSE = OPTION_CAPTURE_UNIVERSE[2:]
__all__ = ["ENTRY_WINDOW_MINUTES", "UNIVERSE", "OptionEarningsCrushBot", "in_entry_window"]


class OptionEarningsCrushBot(OptionUniverseBot):
    RULES: ClassVar[EarningsCrushRules] = EarningsCrushRules()
    DECIDE = decide_earningscrush  # utils/option_strategies.py, shared with the replay backtest

    def __init__(self, **kwargs):
        super().__init__("option_EarningsCrushBot", **kwargs)

    def universe(self):
        return UNIVERSE


if __name__ == "__main__":
    run_bot(OptionEarningsCrushBot)
