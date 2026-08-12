"""
Standalone daily snapshot of real broker equity into live_equity.

The copier already records equity in its finally block, so why a second job?
Because the published track record must not have holes. This one keeps writing on
days the copier errors, days LIVETRADE_DRY_RUN is on, and after the copier is
eventually disabled entirely. An equity curve with gaps is not a track record.

Idempotent per UTC day and safe to run alongside the copier — last write wins.

Broker selection comes from LIVE_EQUITY_BROKER (default "hyperliquid", which is
what the deployed CronJob has always recorded). Credential wiring is the
registry's, not a fourth hand-rolled copy of it.

Run: python -m tradingbot.record_live_equity
"""

import logging
import os
import sys

from dotenv import load_dotenv

from tradingbot.livetrade.equity_recorder import record_live_equity
from tradingbot.livetrade.registry import REGISTRY, ConfigError

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("record_live_equity")


def main() -> int:
    key = os.getenv("LIVE_EQUITY_BROKER", "hyperliquid")
    spec = REGISTRY.get(key)
    if spec is None:
        logger.error(f"Unknown LIVE_EQUITY_BROKER {key!r}. Known brokers: {sorted(REGISTRY)}")
        return 2

    try:
        spec.check_required_env()
        broker = spec.build()
    except ConfigError as e:
        logger.error(str(e))
        return 2

    try:
        broker.connect()
        record_live_equity(broker)
    finally:
        broker.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
