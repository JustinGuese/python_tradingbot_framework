"""Copy paper-bot portfolios onto Interactive Brokers (Web API). Config in livetrade/registry.py.

IB_ACCOUNT_ID selects the account; DU-prefixed ids are paper accounts, and the
adapter reports those as a sandbox so their equity is never recorded as a live
track record.
"""

import sys

from tradingbot.livetrade.registry import REGISTRY
from tradingbot.livetrade.runner import run

if __name__ == "__main__":
    sys.exit(run(REGISTRY["interactive_brokers"]))
