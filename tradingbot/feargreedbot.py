import logging
from utils.core import Bot
from utils.portfolio import get_fear_greed_index

logger = logging.getLogger(__name__)


class FearGreedBotQQQ(Bot):
    def __init__(self, greedindexvalue: float, name: str = "FearGreedBotQQQ"):
        super().__init__(name, "QQQ")

        self.currentFearGreed = greedindexvalue

    def makeOneIteration(self):
        cash = self.dbBot.portfolio.get("USD", 0)
        holding = self.dbBot.portfolio.get(self.symbol, 0)

        if self.bot_name == "FearGreedBotQQQ":
            if self.currentFearGreed >= 70 and cash > 0:
                logger.info(
                    f"Current FearGreed: {self.currentFearGreed} surpassed {70} - Buying QQQ"
                )
                self.buy("QQQ")
                return 1
            elif self.currentFearGreed <= 30 and holding > 0:
                logger.info(
                    f"Current FearGreed: {self.currentFearGreed} below {30} - Selling QQQ"
                )
                self.sell("QQQ")
                return -1
            else:
                if holding > 0:
                    logger.info(
                        f"Current FearGreed: {self.currentFearGreed} is neutral - Holding QQQ"
                    )
                    return 0
                elif cash > 0:
                    logger.info(
                        f"Current FearGreed: {self.currentFearGreed} is neutral - Buying QQQ"
                    )
                    self.buy("QQQ")
                    return 1
                else:
                    logger.info(
                        f"Current FearGreed: {self.currentFearGreed} is neutral - No action"
                    )
                    return 0
        elif self.bot_name == "FearGreedBotQQQInverse":
            if self.currentFearGreed <= 25 and cash > 0:
                logger.info(
                    f"Current FearGreed: {self.currentFearGreed} below {25} - Buying QQQ "
                )
                self.buy("QQQ")
                return 1
            elif self.currentFearGreed >= 75 and holding > 0:
                logger.info(
                    f"Current FearGreed: {self.currentFearGreed} surpassed {75} - Selling QQQ "
                )
                self.sell("QQQ")
                return -1
            else:
                if holding > 0:
                    logger.info(
                        f"Current FearGreed: {self.currentFearGreed} is neutral - Holding QQQ Inverse"
                    )
                    return 0
                elif cash > 0:
                    logger.info(
                        f"Current FearGreed: {self.currentFearGreed} is neutral - Buying QQQ Inverse"
                    )
                    self.buy("QQQ")
                    return 1
                else:
                    logger.info(
                        f"Current FearGreed: {self.currentFearGreed} is neutral - No action"
                    )
                    return 0
        else:
            raise ValueError(f"Unknown bot name: {self.name}")
        return 0



indexvalue = get_fear_greed_index()

fgb = FearGreedBotQQQ(indexvalue or 50)
# fgb.local_development()
fgb.run()
fgbi = FearGreedBotQQQ(indexvalue or 50, name="FearGreedBotQQQInverse")
# fgbi.local_development()
fgbi.run()
