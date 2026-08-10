import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent / "tradingbot"))

from utils.core import Bot


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

# Commodity and crypto beta. Without these, a gold or BTC strategy compared only
# against equity indices shows near-zero correlation by construction, which reads
# as diversification when it is really just a different asset class.
# Both series were backfilled from historic_data to the same inception date as the
# equity benchmarks (2025-12-08, 10,000 USD notional) so they are directly
# comparable; makeOneIteration is a no-op for them because USD is already 0.
bmGLD = BenchmarkBot("Benchmark_GLD", "GLD")
bmGLD.run()

bmBTC = BenchmarkBot("Benchmark_BTC", "BTC-USD")
bmBTC.run()
