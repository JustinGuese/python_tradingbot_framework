"""AlphaTrendDailyBot: daily long/flat alphatrend signal across 19 cross-asset ETFs (paper).

Judged by alpha vs QQQ and t-stat; see docs/backtests/tv-daily-tune-2026-11.md.
"""

from tradingbot.utils.runner import run_bot
from tradingbot.utils.tv_single_signal_bot import TvSingleSignalDailyBot


class AlphaTrendDailyBot(TvSingleSignalDailyBot):
    KIND = "alphatrend"


if __name__ == "__main__":
    run_bot(AlphaTrendDailyBot)
