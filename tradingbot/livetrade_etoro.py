"""Copy paper-bot portfolios onto eToro. Config lives in livetrade/registry.py."""

import sys

from tradingbot.livetrade.registry import REGISTRY
from tradingbot.livetrade.runner import run

if __name__ == "__main__":
    sys.exit(run(REGISTRY["etoro"]))
