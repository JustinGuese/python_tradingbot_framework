from utils.botclass import Bot


class BenchmarkBot(Bot):
    def __init__(self, name, symbol):
        super().__init__(name, symbol)

    def makeOneIteration(self):
        if self.dbBot.portfolio.get("USD", 0) <= 0:
            return 0
        self.buy(self.symbol)


bmQQQ = BenchmarkBot("Benchmark_QQQ", "QQQ")
bmQQQ.run()

bmSPY = BenchmarkBot("Benchmark_SPY", "SPY")
bmSPY.run()

bmFTWD = BenchmarkBot("Benchmark_FTWD", "FTWD.DE")
bmFTWD.run()
