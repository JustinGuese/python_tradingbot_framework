"""
Standalone daily snapshot of real Hyperliquid vault equity into live_equity.

livetrade_hyperliquid.py already records equity in its finally block, so why a
second job? Because the published track record must not have holes. This one
keeps writing on days the copier errors, days LIVETRADE_DRY_RUN is on, and after
the copier is eventually disabled entirely. An equity curve with gaps is not a
track record.

Idempotent per UTC day and safe to run alongside the copier — last write wins.

Run: python -m record_live_equity
"""

import logging
import os

from dotenv import load_dotenv

from livetrade.equity_recorder import record_live_equity
from livetrade.hyperliquid import HyperliquidBroker

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("record_live_equity")


def main():
    private_key = os.getenv("HYPERLIQUID_PRIVATE_KEY")
    if not private_key:
        logger.error("HYPERLIQUID_PRIVATE_KEY must be set")
        return

    broker = HyperliquidBroker(
        private_key=private_key,
        account_address=os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS") or None,
        vault_address=os.getenv("HYPERLIQUID_VAULT_ADDRESS") or None,
        testnet=os.getenv("HYPERLIQUID_TESTNET", "true").lower() == "true",
    )
    record_live_equity(broker)


if __name__ == "__main__":
    main()
