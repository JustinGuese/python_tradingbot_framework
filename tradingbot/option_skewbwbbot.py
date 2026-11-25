"""
option_SkewBWBBot — SPY put broken-wing butterfly (net credit, defined risk) when 25-delta put skew is rich.

Live paper bot. UNPROVEN: no backtest.

Rules (utils/skew_bwb_rules.SkewBWBRules):
  * Underlying: SPY
  * Target DTE: 35
  * Min skew: 0.04 (25-delta put IV minus ATM IV)
  * Max term ratio: 1.0 (VIX / VIX3M gate; unwind if above)
  * Short delta: 0.30
  * Body width: 3% of spot
  * Wing width: 6% of spot (broken wing: lower wing twice the body)
  * Sizing: max_risk_pct of equity (3%)
  * Take profit: 50% of credit
  * Exit DTE: 14

Schedule: 20 16 * * 1-5 (during the US session, live chain).
"""

from __future__ import annotations

import logging
from typing import ClassVar

from tradingbot.utils.option_decide import Action, Holdings, Market
from tradingbot.utils.option_strategy_bot import OptionStrategyBot
from tradingbot.utils.runner import run_bot
from tradingbot.utils.skew_bwb_rules import SkewBWBRules, decide_skewbwb

logger = logging.getLogger(__name__)

UNDERLYING = "SPY"


class OptionSkewBWBBot(OptionStrategyBot):
    """Option strategy bot trading put broken-wing butterflies on SPY when put skew is rich."""

    RULES: ClassVar[SkewBWBRules] = SkewBWBRules(
        underlying=UNDERLYING,
        target_dte=35,
        min_skew=0.04,
        max_term_ratio=1.0,
        short_delta=0.30,
        body_width_pct=0.03,
        wing_width_pct=0.06,
        max_risk_pct=0.03,
        take_profit=0.50,
        exit_dte=14,
    )

    def __init__(self, **kwargs):
        super().__init__(
            "option_SkewBWBBot",
            symbol=self.RULES.underlying,
            interval="1d",
            period="1mo",
            **kwargs,
        )

    def decide(self, market: Market, holdings: Holdings) -> list[Action]:
        return decide_skewbwb(market, holdings, self.RULES, self.RULES.underlying)


RULES = OptionSkewBWBBot.RULES


if __name__ == "__main__":
    run_bot(OptionSkewBWBBot)
