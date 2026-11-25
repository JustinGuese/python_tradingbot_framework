"""
option_PreEarningsStraddleBot — buy an ATM straddle 5-10 sessions before earnings,
delta-hedge daily, sell the session before the report.

Rides the implied volatility run-up into earnings, never holds through the jump.

UNPROVEN: no backtest exists and none is claimed.

Schedule: 30 19 * * 1-5 (before a normal close; 16:30 early close handled by the entry window, as in EarningsCrush).
"""

from __future__ import annotations

import logging
from typing import ClassVar

from tradingbot.utils.option_strategy_bot import OptionUniverseBot
from tradingbot.utils.preearnings_straddle_rules import (
    DEFAULT_ENTRY_WINDOW_MINUTES,
    PreEarningsStraddleRules,
    decide_preearnings,
    in_entry_window,
)
from tradingbot.utils.runner import run_bot
from tradingbot.utils.universes import OPTION_CAPTURE_UNIVERSE

logger = logging.getLogger(__name__)

UNIVERSE: tuple[str, ...] = OPTION_CAPTURE_UNIVERSE[2:]
ENTRY_WINDOW_MINUTES = DEFAULT_ENTRY_WINDOW_MINUTES

__all__ = [
    "ENTRY_WINDOW_MINUTES",
    "RULES",
    "UNIVERSE",
    "OptionPreEarningsStraddleBot",
    "in_entry_window",
]


class OptionPreEarningsStraddleBot(OptionUniverseBot):
    """
    Option strategy bot buying pre-earnings straddles across captured names.
    UNPROVEN: no backtest.
    """

    RULES: ClassVar[PreEarningsStraddleRules] = PreEarningsStraddleRules()
    UNIVERSE: ClassVar[tuple[str, ...]] = UNIVERSE
    DECIDE = decide_preearnings

    def __init__(self, **kwargs):
        super().__init__("option_PreEarningsStraddleBot", **kwargs)

    def universe(self) -> tuple[str, ...]:
        return self.UNIVERSE


# Module-level alias for Helm chart inspection / test discovery
RULES = OptionPreEarningsStraddleBot.RULES


if __name__ == "__main__":
    run_bot(OptionPreEarningsStraddleBot)
