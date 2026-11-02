"""
OpenRangeBreakoutBot — QQQ, long-only: buy the first close above the opening range
inside the first 90 minutes, take a small profit (0.35R) with a wide stop (the
range low), and be flat when the window ends. Wins often and loses big; judge it
on expectancy after costs, never win rate (see tradingbot/utils/orb_rules.py).

Schedule: every 5 minutes across the US session (cron covers both EST and EDT).
"""

from typing import ClassVar

from tradingbot.utils.intraday_bot import IntradayRulesBot
from tradingbot.utils.orb_rules import ORBRules
from tradingbot.utils.runner import run_bot


class OpenRangeBreakoutBot(IntradayRulesBot):
    param_grid: ClassVar[dict] = {
        "or_minutes": [5, 15, 30],
        "target_r": [0.25, 0.35, 0.5, 1.0],
        "window_minutes": [60, 90],
    }

    def __init__(self, or_minutes: int = 15, target_r: float = 0.35, window_minutes: int = 90, **kwargs):
        super().__init__(
            "OpenRangeBreakoutBot",
            ORBRules(or_minutes=or_minutes, target_r=target_r, window_minutes=window_minutes),
            **kwargs,
        )


if __name__ == "__main__":
    run_bot(OpenRangeBreakoutBot)
