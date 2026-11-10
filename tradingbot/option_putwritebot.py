"""
option_PutWriteBot — cash-secured put writing on SPY/QQQ with curve-slope and trend gates.

Rules (utils/putwrite_rules.PutWriteRules):
  * Cash-secured short puts (cash covers strike x 100).
  * Delta: put_delta (e.g. 0.25).
  * Target DTE: target_dte (e.g. 35).
  * Gate: max VIX/VIX3M (e.g. 1.0 or None).
  * Gate: trend filter (close > SMA200 when enabled).
  * Gate: min IV-fair gap (ATM IV - HAR-RV fair >= min_gap).
  * Exit: take profit at share of credit, or roll/close at exit_dte left.

Schedule: 45 15 * * 1-5 (chain must be live to open).
"""

from __future__ import annotations

import logging
from typing import ClassVar

from tradingbot.utils.option_decide import Action, Holdings, Market
from tradingbot.utils.option_strategy_bot import OptionStrategyBot
from tradingbot.utils.putwrite_rules import PutWriteRules, decide_putwrite
from tradingbot.utils.runner import run_bot

logger = logging.getLogger(__name__)


class OptionPutWriteBot(OptionStrategyBot):
    RULES: ClassVar[PutWriteRules] = PutWriteRules(
        underlying="SPY",
        put_delta=0.25,
        target_dte=35,
        max_term_ratio=None,
        trend_filter=False,
        min_gap=None,
        take_profit=0.50,
        exit_dte=21,
    )

    def __init__(self, **kwargs):
        super().__init__(
            "option_PutWriteBot",
            symbol=self.RULES.underlying,
            interval="1d",
            period="5y",
            **kwargs,
        )

    def decide(self, market: Market, holdings: Holdings) -> list[Action]:
        return decide_putwrite(market, holdings, self.RULES, self.RULES.underlying)


if __name__ == "__main__":
    run_bot(OptionPutWriteBot)
