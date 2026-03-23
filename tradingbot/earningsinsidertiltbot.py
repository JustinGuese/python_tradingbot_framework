"""
Earnings + insider tilt bot: equal-weight base, tilt by earnings/insider scores, rebalance.
All scoring and tilting logic lives in `utils.portfolio`; this bot only orchestrates.
"""

from utils.core import Bot
from utils.portfolio import TRADEABLE, earnings_insider_compute_weights


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
    bot = EarningsInsiderTiltBot()
    bot.run()