"""SupertrendExEquityBot: daily long/flat supertrend signal across 8 non-equity ETFs (paper).

Walk-forward study (docs/backtests/tv-trend-boost-2026-11.md): ex-equity universe lifts OOS alpha t
and cuts beta to ~0.03. Judged by alpha vs QQQ and t-stat.
"""

from tradingbot.utils.runner import run_bot
from tradingbot.utils.tv_signals import UNIVERSE_EX_EQUITY
from tradingbot.utils.tv_single_signal_bot import TvSingleSignalDailyBot


class SupertrendExEquityBot(TvSingleSignalDailyBot):
    KIND = "supertrend"
    UNIVERSE = UNIVERSE_EX_EQUITY


if __name__ == "__main__":
    run_bot(SupertrendExEquityBot)
