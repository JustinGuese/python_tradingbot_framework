"""SlingshotChandelierDailyBot: daily long/flat slingshot_chandelier signal across 19 cross-asset ETFs (paper).

Judged by alpha vs QQQ and t-stat; see docs/backtests/tv-daily-tune-2026-11.md.
"""

from tradingbot.utils.runner import run_bot
from tradingbot.utils.tv_single_signal_bot import TvSingleSignalDailyBot


class SlingshotChandelierDailyBot(TvSingleSignalDailyBot):
    KIND = "slingshot_chandelier"


if __name__ == "__main__":
    run_bot(SlingshotChandelierDailyBot)
