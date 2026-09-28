"""
option_DispersionBot — short index vol against long single-name vol when the
options price the S&P's members as unusually correlated.

Index variance is the members' variances plus their covariances, so index
implied vol embeds an implied correlation. When that correlation is rich,
selling the index's vol and buying its members' is the classic dispersion
trade: it earns when the members move more independently than priced.

Rules (utils/option_rules.DispersionRules):
  * Signal: implied correlation of SPY against the captured names
    (implied_correlation table, written after each daily capture), as a
    percentile of its own history. Trades nothing until min_history (60)
    observations exist, and says so on every run.
  * Enter at >= entry_pct (80th): short SPY iron butterfly (wings at
    wing_sigmas x the expected move), long ATM straddles on the n_names
    largest names by market cap, lots per name N_i = |V_fly| x w_i / V_i so
    each carries its weight's share of the index leg's vega.
  * Size: fly worst-case loss plus straddle premiums <= max_risk_pct of book.
  * Exit at <= exit_pct, exit_dte left, or take_profit of the capital at stake.

Short dispersion only: the reverse trade needs naked short single-name vol.
It loses when correlation jumps toward 1, which is a crash: the tail every
short-index-vol book carries, here partly offset by the long straddles.

Evidence: none. Paper, unproven, live-only; the implied-correlation history
it needs starts with the capture (2026-09-28).

Schedule: 55 15 * * 1-5.
"""

import logging
from typing import ClassVar

from tradingbot.utils.option_decide import Action, Holdings, Market
from tradingbot.utils.option_rules import DispersionRules
from tradingbot.utils.option_strategies import decide_dispersion
from tradingbot.utils.option_strategy_bot import OptionUniverseBot
from tradingbot.utils.runner import run_bot
from tradingbot.utils.universes import OPTION_CAPTURE_UNIVERSE

logger = logging.getLogger(__name__)

INDEX = "SPY"
UNIVERSE = OPTION_CAPTURE_UNIVERSE[2:]


class OptionDispersionBot(OptionUniverseBot):
    RULES: ClassVar[DispersionRules] = DispersionRules()

    def __init__(self, **kwargs):
        super().__init__("option_DispersionBot", **kwargs)

    def decide(self, market: Market, holdings: Holdings) -> list[Action]:
        # utils/option_strategies.decide_dispersion, shared with the replay backtest.
        return decide_dispersion(market, holdings, self.RULES, UNIVERSE, INDEX)


if __name__ == "__main__":
    run_bot(OptionDispersionBot)
