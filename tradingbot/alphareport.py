"""
Weekly alpha vs QQQ for every live bot, into `bot_alpha_report`.

CLAUDE.md's bar, applied every Saturday instead of by hand:
  * data: each bot's portfolio_worth series over its own live window;
  * against: Benchmark_QQQ over the same dates;
  * cleaning: weekend rows dropped, and day pairs more than 4 days apart
    dropped;
  * reported: alpha, t, beta, correlation and max drawdown.

The verdicts:
  * "edge" (t >= 2);
  * "pause candidate" (t <= -2);
  * "levered QQQ" (corr >= 0.8 and beta >= 1.5);
  * "QQQ clone" (beta and corr >= 0.8);
  * "unproven";
  * "too short" (under 60 daily returns).

The ranked table goes to the log and a pause candidate is a WARNING. Nothing
is paused automatically; that stays a values.yaml decision.

Schedule: 0 7 * * 6. Exits 1 if Benchmark_QQQ is missing or more than 4 days
stale, because every row would then be measured against the wrong thing.
See utils/alpha_report.py.
"""

import logging
import sys

from tradingbot.utils.alpha_report import run_weekly_report
from tradingbot.utils.config import setup_logging
from tradingbot.utils.db import init_db

logger = logging.getLogger(__name__)


def main() -> int:
    setup_logging()
    init_db()
    try:
        rows = run_weekly_report()
    except LookupError as e:
        logger.error("alpha report not written: %s", e)
        return 1
    logger.info("alpha report: %d bots written", len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
