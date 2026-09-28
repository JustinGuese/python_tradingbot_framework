"""
Weekly refresh of `macro_events`: FOMC statement days, CPI prints and jobs
reports, past and scheduled (utils/macro_calendar.py).

Option bots that avoid opening short vol right before an event read the next
one from this table. Needs FRED_API_KEY (CPI and NFP come from FRED's release
calendar; FOMC is a static table in the module).

Schedule: 0 6 * * 1. Exits non-zero when the key is missing or FRED returns
no CPI/NFP dates, so a broken refresh turns the CronJob red instead of letting
the calendar go quietly stale.
"""

import logging
import os
import sys

from tradingbot.utils.config import setup_logging
from tradingbot.utils.db import init_db
from tradingbot.utils.macro_calendar import refresh_macro_events

logger = logging.getLogger(__name__)


def main() -> int:
    setup_logging()
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if not api_key:
        logger.error("FRED_API_KEY is not set: cannot refresh CPI/NFP dates")
        return 1
    init_db()
    counts = refresh_macro_events(api_key)
    if not counts.get("CPI") or not counts.get("NFP"):
        logger.error("FRED returned no CPI or NFP dates (%s) — treating as a failed run", counts)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
