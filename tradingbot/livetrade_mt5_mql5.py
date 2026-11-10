"""Copy paper-bot portfolios onto MetaTrader 5 (MQL5 Signals). Config lives in livetrade/registry.py."""

import sys

from tradingbot.livetrade.registry import REGISTRY
from tradingbot.livetrade.runner import run

if __name__ == "__main__":
    sys.exit(run(REGISTRY["mt5_mql5"]))
