"""
option_CrossVolBot — sell iron condors on the single names whose implied vol
sits furthest above a forecast of the vol they will deliver.

The AAPL mispricing bot found the gap between implied and forecast vol real,
but a short structure on ONE stock gives it back in that stock's jumps. This
spreads the same trade over the 50 captured names (utils/universes
.OPTION_CAPTURE_UNIVERSE), a few at a time, so one name's jump is a fraction
of the book instead of all of it.

Rules (utils/option_rules.CrossVolRules):
  * Fair vol: Yang-Zhang HAR forecast over the sessions to expiry, earnings
    reaction days excluded (utils/mispricing_scan.live_name_vol).
  * Shortlist: the `shortlist` names with the largest |z| in yesterday's scan
    (option_mispricing_scan); before the first scan, the universe in order.
  * Sell only names with no earnings before expiry, gap = ATM IV - fair
    >= min_gap, VIX < max_vix; richest gap first, up to max_positions names.
  * Each: 10-delta short put and call, wings width_pct of spot further out,
    worst-case loss <= risk_per_name_pct of the book.
  * Close at take_profit of the credit, stop_loss x the credit, exit_dte left,
    or before an ex-dividend date threatens early assignment of the short call.

Evidence: none yet. It cannot be backtested until per-name option history
exists (utils/option_replay.py over option_quotes). Paper, unproven,
live-only; it is the plain-gap baseline the mispricing scanner is compared to.

Schedule: 50 15 * * 1-5 (the chain must be live to open).
"""

import logging
from typing import ClassVar

from tradingbot.utils.option_rules import CrossVolRules
from tradingbot.utils.option_strategies import decide_crossvol
from tradingbot.utils.option_strategy_bot import OptionUniverseBot
from tradingbot.utils.runner import run_bot
from tradingbot.utils.universes import OPTION_CAPTURE_UNIVERSE

logger = logging.getLogger(__name__)

UNIVERSE = OPTION_CAPTURE_UNIVERSE[2:]  # the single names; SPY/QQQ are the index bot's


class OptionCrossVolBot(OptionUniverseBot):
    RULES: ClassVar[CrossVolRules] = CrossVolRules()
    DECIDE = decide_crossvol  # utils/option_strategies.py, shared with the replay backtest

    def __init__(self, **kwargs):
        super().__init__("option_CrossVolBot", **kwargs)

    def universe(self):
        return UNIVERSE


if __name__ == "__main__":
    run_bot(OptionCrossVolBot)
