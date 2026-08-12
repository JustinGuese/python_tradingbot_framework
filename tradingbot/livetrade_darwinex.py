"""Copy paper-bot portfolios onto Darwinex DXtrade. Config lives in livetrade/registry.py.

Darwinex DXtrade carries FX, indices and commodities only — no equities or ETFs.
A bot whose universe is equities cannot be copied here regardless of weights.
"""

import sys

from tradingbot.livetrade.registry import REGISTRY
from tradingbot.livetrade.runner import run

if __name__ == "__main__":
    sys.exit(run(REGISTRY["darwinex"]))
