import logging

from tradingbot.utils.botclass import Bot
from tradingbot.utils.portfolio_utils import get_fear_greed_index

logger = logging.getLogger(__name__)

# FearGreedBotQQQInverse: buy QQQ in fear, go flat once sentiment is back to
# neutral, and never buy on a neutral reading. It used to buy on neutral too,
# which kept it in the market 94% of the time — a QQQ clone (beta 0.97, alpha
# +0.3%/yr, t 0.19 over 2021-01..2026-09 on CNN's daily history). Flat-by-default
# at 30/50: beta ~0.5, max drawdown -23% vs -35%, alpha +4.5%/yr (t 0.97). By
# half: +7.6% in 2021-23 and +0.5% in 2024-26, so it is mostly a bear-market
# edge. What it reliably does is halve beta without giving up alpha. 30/50 is
# a middle cell of the positive region (buy 25-30, exit 45-60), not the best
# one, to avoid fitting the grid.
INVERSE_BUY_AT_OR_BELOW = 30
INVERSE_EXIT_AT_OR_ABOVE = 50


class FearGreedBotQQQ(Bot):
    def __init__(self, greedindexvalue: float, name: str = "FearGreedBotQQQ"):
        super().__init__(name, "QQQ")

        self.currentFearGreed = greedindexvalue

    def makeOneIteration(self):
        cash = self.dbBot.portfolio.get("USD", 0)
        holding = self.dbBot.portfolio.get(self.symbol, 0)

        if self.bot_name == "FearGreedBotQQQ":
            if self.currentFearGreed >= 70 and cash > 0:
                logger.info(f"Current FearGreed: {self.currentFearGreed} surpassed {70} - Buying QQQ")
                self.buy("QQQ")
                return 1
            elif self.currentFearGreed <= 30 and holding > 0:
                logger.info(f"Current FearGreed: {self.currentFearGreed} below {30} - Selling QQQ")
                self.sell("QQQ")
                return -1
            else:
                if holding > 0:
                    logger.info(f"Current FearGreed: {self.currentFearGreed} is neutral - Holding QQQ")
                    return 0
                elif cash > 0:
                    logger.info(f"Current FearGreed: {self.currentFearGreed} is neutral - Buying QQQ")
                    self.buy("QQQ")
                    return 1
                else:
                    logger.info(f"Current FearGreed: {self.currentFearGreed} is neutral - No action")
                    return 0
        elif self.bot_name == "FearGreedBotQQQInverse":
            if self.currentFearGreed <= INVERSE_BUY_AT_OR_BELOW and cash > 0:
                logger.info(f"FearGreed {self.currentFearGreed} <= {INVERSE_BUY_AT_OR_BELOW} (fear) - Buying QQQ")
                self.buy("QQQ")
                return 1
            elif self.currentFearGreed >= INVERSE_EXIT_AT_OR_ABOVE and holding > 0:
                logger.info(f"FearGreed {self.currentFearGreed} >= {INVERSE_EXIT_AT_OR_ABOVE} - fear over, going flat")
                self.sell("QQQ")
                return -1
            else:
                logger.info(f"FearGreed {self.currentFearGreed} - holding current position (QQQ: {holding})")
                return 0
        else:
            # self.bot_name, not self.name: the base class stores the bot's
            # identity as bot_name (botclass.py) and never defines .name, so
            # this line used to raise AttributeError instead of the intended
            # ValueError — masking the actual misconfiguration.
            raise ValueError(f"Unknown bot name: {self.bot_name}")


# Guarded: without this, importing this module executes bot.run() and trades a
# live paper portfolio as a side effect of the import. The Helm CronJob invokes
# `python <name>.py`, so __name__ == "__main__" and production is unchanged.
if __name__ == "__main__":
    indexvalue = get_fear_greed_index()

    fgb = FearGreedBotQQQ(indexvalue or 50)
    # fgb.local_development()
    fgb.run()
    fgbi = FearGreedBotQQQ(indexvalue or 50, name="FearGreedBotQQQInverse")
    # fgbi.local_development()
    fgbi.run()
