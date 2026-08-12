"""
Earnings + insider tilt bot: equal-weight base, tilt by earnings/insider scores, rebalance.
All scoring and tilting logic lives in `utils.earnings_insider`; this bot only orchestrates.
"""

from tradingbot.utils.botclass import Bot
from tradingbot.utils.config import TRADEABLE
from tradingbot.utils.earnings_insider import earnings_insider_compute_weights
from tradingbot.utils.runner import run_bot


class EarningsInsiderTiltBot(Bot):
    """
    Bot that rebalances using equal-weight base, tilted by earnings and insider scores.
    """

    def __init__(self):
        super().__init__("EarningsInsiderTiltBot", symbol=None)
        self.tradeable_symbols = TRADEABLE

    def makeOneIteration(self):
        """
        Compute base weights (equal), score symbols, tilt weights, rebalance.
        Returns 0.
        """
        syms = self.tradeable_symbols
        if not syms:
            return 0
        weights = earnings_insider_compute_weights(syms)
        self.rebalancePortfolio(weights, onlyOver50USD=True)
        return 0


if __name__ == "__main__":
    run_bot(EarningsInsiderTiltBot)
