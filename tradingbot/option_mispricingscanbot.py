"""
option_MispricingScanBot — price every name in vol terms against a forward
forecast, and trade only what is unusually rich or cheap for THAT name.

The raw gap between implied and forecast vol is not a signal on its own:
implied sits above realized most of the time (the variance risk premium), so
"IV > forecast" says sell nearly every day. This bot z-scores the gap against
the name's own history and acts only at the tails, in both directions, with
risk measured across the whole book.

Rules (utils/option_rules.MispricingScanRules, scan in utils/mispricing_scan):
  * Universe: SPY, QQQ and the 50 captured names. Shortlist: SPY, QQQ, then the
    largest |z| in yesterday's scan; live chains are loaded for those only.
  * Forecast: Yang-Zhang HAR over the sessions to expiry, earnings days out.
  * z: today's ATM IV minus a HAR forecast, against the name's vol_surface
    history of the same (SPY/QQQ: a ^VIX/^VXN proxy back to 2001). Single
    names need 60 days of their own history first and are logged "n/60".
  * Rich (z >= +2, no earnings before expiry, VIX < 35): an iron condor, short
    strikes at 16 delta, wings 8% of spot. Cheap (z <= -2): a long ATM
    straddle, delta-hedged inside a Whalley-Wilmott band.
  * Surface filters: strike pairs outside the American put-call band are
    dropped as bad quotes; the SVI fit's outliers are logged for the record.
  * Sizing by vega: each position aims at 1/max_positions of a book-wide vega
    budget (vega_budget_pct x equity per vol point), and is skipped unless the
    whole book stays inside that budget and a crash (every underlying -20%,
    vol +30 points) costs at most stress_cap_pct of equity.
  * Unwind every short-vol position when ^VIX / ^VIX3M inverts past
    unwind_term_ratio. Exits otherwise at take-profit, stop, DTE, max hold,
    or |z| back under exit_z; short calls are closed before an ex-dividend
    date threatens early assignment.

Evidence: the SPY/QQQ z-score leg is the only part a backtest can reach
(scripts/onetime_index_vol_backtest.py --gates, grid B). The single-name and
surface parts are live-only until utils/option_replay.py has history.
Paper, unproven.

Schedule: 0 16 * * 1-5.
"""

import logging
from typing import ClassVar

from tradingbot.utils.option_decide import Action, Holdings, Market
from tradingbot.utils.option_rules import MispricingScanRules
from tradingbot.utils.option_strategies import decide_mispricingscan
from tradingbot.utils.option_strategy_bot import OptionUniverseBot
from tradingbot.utils.runner import run_bot
from tradingbot.utils.universes import OPTION_CAPTURE_UNIVERSE

logger = logging.getLogger(__name__)

UNIVERSE = OPTION_CAPTURE_UNIVERSE
ALWAYS = ("SPY", "QQQ")


class OptionMispricingScanBot(OptionUniverseBot):
    RULES: ClassVar[MispricingScanRules] = MispricingScanRules()

    def __init__(self, **kwargs):
        super().__init__("option_MispricingScanBot", **kwargs)

    def decide(self, market: Market, holdings: Holdings) -> list[Action]:
        # utils/option_strategies.decide_mispricingscan, shared with the replay backtest.
        return decide_mispricingscan(market, holdings, self.RULES, UNIVERSE, ALWAYS)


if __name__ == "__main__":
    run_bot(OptionMispricingScanBot)
