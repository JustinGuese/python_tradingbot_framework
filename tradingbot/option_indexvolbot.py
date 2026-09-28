"""
option_IndexVolBot — sell SPY iron condors when implied vol beats a forecast
of the vol SPY will actually deliver.

The AAPL option bots found that the gap between implied vol and a HAR-RV
forecast predicts the variance premium, but a short structure on one stock
gives it back in single-name jumps. An index jumps less and pays the larger
premium, and nobody picked SPY with hindsight.

Rules (utils/option_rules.IndexVolRules):
  * Fair vol: HAR-RV forecast of SPY's realized vol over the trading days to
    expiry (option_math.har_rv_forecast). No earnings term: an index has none.
  * Sell only when ATM IV - fair vol >= min_gap and ^VIX < 40.
  * Short put at put_delta and short call at call_delta on the first expiry
    >= target_dte days out, each with a long wing width_pct of spot further
    out. Worst-case loss (a wing minus the credit) <= max_risk_pct of the book.
  * Close at take_profit of the credit, at a loss of stop_loss x the credit,
    or at exit_dte days left.

Walk-forward (2026-09-26, synthetic: Black-Scholes at 0.85 x ^VIX with the
live chain's skew, 2000-2026). The textbook defaults (16-delta, 5% wings, 45
DTE) made t 0.75 and lost on 2013-2026. The consensus of the 2000-2013 top 10
ships instead: 10-delta shorts, 10% wings, 35 DTE, exit at 7 DTE, and only when
ATM IV >= HAR fair + 3 points. Alpha +2.0%/yr at t 4.28, beta 0.01, max DD
-6.4%, positive in both halves. It also holds at double costs (t 3.16), on QQQ
(t 2.39) and with half the skew. The single 2000-2013 winner, without the gap
gate, failed out of sample; the gate is what matters. Small but uncorrelated.
See docs/backtests/index-vol-2026-09.md. Paper only.

Round 3 (2026-09-28) tested VIX/VIX3M and VVIX entry gates, an FOMC blackout,
a term-structure unwind, Yang-Zhang and GARCH forecasts, a z-scored signal and
a monthly tail hedge on top of these rules. None beat them on alpha t out of
sample (docs/backtests/option-round3-2026-09.md).

One ships anyway, as a deliberate drawdown-over-t choice that overrides the
walk-forward rule: no new condor while VIX/VIX3M > 1.0 (an inverted vol curve,
i.e. the market already pricing near-term stress). On 2007-2026 it more than
halves max drawdown (-2.7% vs -6.3%) for 56 trades instead of 80, and keeps
t >= 2 in both halves (H1 2.84, H2 2.65) where the ungated rules made H1 1.67,
H2 3.46. A missing ^VIX3M quote blocks the entry. The bot still logs the vol
curve and the next FOMC/CPI date on every run.

Schedule: 45 15 * * 1-5 (the chain must be live to open).
"""

import logging
from typing import ClassVar

from tradingbot.utils.option_decide import Action, Holdings, Market
from tradingbot.utils.option_rules import IndexVolRules
from tradingbot.utils.option_strategies import decide_indexvol
from tradingbot.utils.option_strategy_bot import OptionStrategyBot
from tradingbot.utils.runner import run_bot

logger = logging.getLogger(__name__)

UNDERLYING = "SPY"


class OptionIndexVolBot(OptionStrategyBot):
    # max_term_ratio=1.0 lost to the ungated rules on H2 alpha t (2.65 vs 3.46)
    # but halves max DD; shipped for the drawdown on purpose (see the docstring).
    RULES: ClassVar[IndexVolRules] = IndexVolRules(
        target_dte=35, put_delta=0.10, call_delta=0.10, width_pct=0.10, min_gap=0.03, exit_dte=7, max_term_ratio=1.0
    )

    def __init__(self, **kwargs):
        super().__init__("option_IndexVolBot", symbol=UNDERLYING, interval="1d", period="5y", **kwargs)

    def decide(self, market: Market, holdings: Holdings) -> list[Action]:
        # The logic is utils/option_strategies.decide_indexvol, shared with the
        # replay and synthetic backtests.
        return decide_indexvol(market, holdings, self.RULES, UNDERLYING)


if __name__ == "__main__":
    run_bot(OptionIndexVolBot)
