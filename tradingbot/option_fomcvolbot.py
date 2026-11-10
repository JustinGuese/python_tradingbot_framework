"""
option_FOMCVolBot — trade index event volatility around FOMC rate decisions.

The bot enters at the close of the session immediately preceding an FOMC statement
(session T-1) and exits after the 14:00 New York statement release (session T0).

Rules (utils/fomc_vol_rules.FOMCVolRules):
  * Underlying: SPY
  * Structure: defined-risk iron fly (long ATM straddle financed by short wings at 5% of spot)
  * Side: long (captures announcement jumps while short wings finance the debit)
  * Entry: last run before the close of the session before FOMC (14:00 - 16:00 NY)
  * Exit: first run after the 14:00 announcement on the FOMC session
  * Sizing: max_risk_pct of book equity (default 5%)

Walk-forward study (2006 -> 2026, split 2019-05; docs/backtests/fomc-vol-2026-11.md):
  * Short straddle / short iron fly (the retail textbook) fails out of sample (H2 t = -3.51).
    Realized jumps around FOMC statements exceed priced implied vol.
  * Long iron fly (5% wings) wins on H1 (t = +4.04) and replicates on H2 (t = +3.51).
  * Shipped live paper: long iron fly, 5% wings, SPY front expiry.

Schedule: 45 15 * * 1-5 (runs before close to enter on T-1 and exit after 14:00 announcement on T0).
"""

from __future__ import annotations

import logging
from typing import ClassVar

from tradingbot.utils.fomc_vol_rules import FOMCVolRules, decide_fomcvol
from tradingbot.utils.option_decide import Action, Holdings, Market
from tradingbot.utils.option_strategy_bot import OptionStrategyBot
from tradingbot.utils.runner import run_bot

logger = logging.getLogger(__name__)

UNDERLYING = "SPY"


class OptionFOMCVolBot(OptionStrategyBot):
    """Option strategy bot trading FOMC event volatility on SPY."""

    RULES: ClassVar[FOMCVolRules] = FOMCVolRules(
        side="long",
        structure="iron_fly",
        wing_width_pct=0.05,
        strike_offset=0.0,
        target_dte=1,
        max_risk_pct=0.05,
    )

    def __init__(self, **kwargs):
        super().__init__("option_FOMCVolBot", symbol=UNDERLYING, interval="1d", period="1mo", **kwargs)

    def decide(self, market: Market, holdings: Holdings) -> list[Action]:
        return decide_fomcvol(market, holdings, self.RULES, UNDERLYING)


# Module-level alias for Helm chart inspection / test discovery
RULES = OptionFOMCVolBot.RULES


if __name__ == "__main__":
    run_bot(OptionFOMCVolBot)
