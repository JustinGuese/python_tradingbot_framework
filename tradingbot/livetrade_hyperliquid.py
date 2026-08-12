"""
Copy paper-bot portfolios onto Hyperliquid perps, optionally into a user vault.

Safety model — three layers, all of which must hold:

1. LEVERAGE 1x CROSS. HyperliquidBroker forces it before the first opening order
   on each coin, so the exchange itself rejects any order that would push total
   notional above account value. This is the layer that holds even if the
   copier's weight maths is wrong.
2. PORTFOLIO_FRACTION 0.95, not 1.0. The copier clamps buys to
   get_cash() * 0.98; at fraction 1.0 the target notional exceeds that budget
   every run, so it scales every order down and sits on the margin boundary.
   At 0.95 the scaling branch never fires. Set as the registry default.
3. LONG ONLY. The broker refuses SELLs with no open long and clamps oversized
   SELLs to the position, so a short can never be opened by accident.

Reads are scoped to HYPERLIQUID_VAULT_ADDRESS when set. Without it, this trades
the leader's own account — deliberately allowed for the mainnet smoke stage, but
the registry builder warns loudly.

Equity is recorded after every run (see livetrade/runner.py); record_live_equity.py
additionally covers the days this job does not run at all.
"""

import sys

from tradingbot.livetrade.registry import REGISTRY
from tradingbot.livetrade.runner import run

if __name__ == "__main__":
    sys.exit(run(REGISTRY["hyperliquid"]))
