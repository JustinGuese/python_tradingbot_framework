"""
VWAPPullbackBot — QQQ, long-only intraday: buy the first pullback into VWAP after
the opening range breaks up. Stop below the recent range, target 2R, flat before
the close. A plain VWAP cross is deliberately not traded (see
tradingbot/utils/vwap_pullback_rules.py).

Schedule: every 5 minutes across the US session (cron covers both EST and EDT;
the rules decide whether a session is actually open).
"""

from typing import ClassVar

from tradingbot.utils.intraday_bot import IntradayRulesBot
from tradingbot.utils.runner import run_bot
from tradingbot.utils.vwap_pullback_rules import VWAPPullbackRules


class VWAPPullbackBot(IntradayRulesBot):
    param_grid: ClassVar[dict] = {
        "or_minutes": [5, 15, 30],
        "swing_bars": [3, 6, 9],
        "target_r": [1.5, 2.0, 3.0],
    }

    def __init__(self, or_minutes: int = 15, swing_bars: int = 6, target_r: float = 2.0, **kwargs):
        super().__init__(
            "VWAPPullbackBot",
            VWAPPullbackRules(or_minutes=or_minutes, swing_bars=swing_bars, target_r=target_r),
            **kwargs,
        )


if __name__ == "__main__":
    run_bot(VWAPPullbackBot)
