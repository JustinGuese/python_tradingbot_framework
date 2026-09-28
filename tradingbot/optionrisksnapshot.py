"""
Daily risk snapshot of every option bot's book into `option_risk`
(utils/option_risk.py): dollar greeks, spot/vol stress P&L, max loss, margin.

Schedule: 30 21 * * 1-5, after the close. Exits non-zero if any book failed
to value, so a broken mark turns the CronJob red. No option books at all is a
normal, successful run.
"""

import logging
import sys

from tradingbot.utils.config import setup_logging
from tradingbot.utils.db import init_db
from tradingbot.utils.option_risk import snapshot_option_risk

logger = logging.getLogger(__name__)


def main() -> int:
    setup_logging()
    init_db()
    result = snapshot_option_risk()
    if result["failed"]:
        logger.error("option risk: %d books failed to value: %s", len(result["failed"]), result["failed"])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
