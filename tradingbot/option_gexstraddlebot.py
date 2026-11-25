"""
option_GexStraddleBot — SPY 0DTE straddle on negative-gamma days.

Dealer GEX < 0 and VIX/VIX3M > 1 (backwardation), 2x take-profit, flat by 15:45 NY.
Label: UNPROVEN: no backtest.

Rules (utils/gex_straddle_rules.GexStraddleRules):
  * Underlying: SPY
  * Window: 10:00 - 11:00 NY
  * Gate: GEX < 0, VIX/VIX3M > 1
  * Structure: 0DTE ATM straddle (select_straddle)
  * Exit: +100% PnL (+1.0) or 15:45 NY force-close
  * Sizing: 0.5% max risk per ticket

Schedule: */5 14-20 * * 1-5 (covers EST/EDT trading hours; decide gates by NY time).
"""

from __future__ import annotations

import logging
from typing import ClassVar

from tradingbot.utils.gex_straddle_rules import GexStraddleRules, decide_gexstraddle
from tradingbot.utils.option_decide import Action, Holdings, Market
from tradingbot.utils.option_strategy_bot import OptionStrategyBot
from tradingbot.utils.runner import run_bot

logger = logging.getLogger(__name__)

UNDERLYING = "SPY"


class OptionGexStraddleBot(OptionStrategyBot):
    """Option strategy bot trading 0DTE straddles on SPY during negative gamma regimes.

    Live paper. UNPROVEN: no backtest.
    """

    RULES: ClassVar[GexStraddleRules] = GexStraddleRules()

    def __init__(self, **kwargs):
        super().__init__("option_GexStraddleBot", symbol=UNDERLYING, interval="1d", period="1mo", **kwargs)

    def decide(self, market: Market, holdings: Holdings) -> list[Action]:
        return decide_gexstraddle(market, holdings, self.RULES, UNDERLYING)


# Module-level alias for inspection / test discovery
RULES = OptionGexStraddleBot.RULES


if __name__ == "__main__":
    run_bot(OptionGexStraddleBot)
