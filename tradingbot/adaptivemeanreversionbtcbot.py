"""
Adaptive Mean Reversion — BTC variant.

Same signal as AdaptiveMeanReversionBot:
  BUY  : close > SMA-200  AND  ATR < atr_multiplier × ATR-MA20
  SELL : close < SMA-200 × (1 - sell_buffer)
retargeted at BTC-USD with crypto-appropriate parameters.

This is the strategy published as the Hyperliquid user vault. It is a SEPARATE
DB bot from AdaptiveMeanReversionBot on purpose: that bot's portfolio_worth
history is the NilssonHedge submission and must stay continuous and unmodified.
Never retarget the parent — always subclass.

Why the signal ports and gptbasedstrategytabased does not:
  "Long above the 200-day SMA unless volatility is extreme" is a regime filter
  with decades of cross-asset evidence, and it holds a position most of the time
  — which a public vault needs, because a NAV that is flat for months reads as
  dead. gptbasedstrategytabased is the only other native-BTC bot, but its entry
  requires six indicators to align simultaneously and its recorded backtest rests
  on three trades. That is an anecdote, not a track record.

Why a wider sell_buffer than the QQQ parent:
  BTC's daily volatility is roughly 3-4x QQQ's, so the parent's 3% band around
  the SMA-200 fires on ordinary noise and whipsaws. ~15% is the crypto analogue.
  These are starting values chosen by analogy — see the TODO below.

TODO before going live (rollout stage 3+):
  Run local_optimize() over param_grid on period="max" and paste the backtest
  footer below in house style. Do NOT publish a vault on parameters that were
  never fitted to BTC.

Schedule: "50 23 * * *" — 23:50 UTC, 7 days a week. yfinance BTC-USD daily bars
roll at 00:00 UTC, so this acts on an essentially complete bar. The Hyperliquid
copier (livetrade_hyperliquid.py) runs at 00:05 UTC, 15 minutes later.
"""

import logging

from adaptivemeanreversionbot import AdaptiveMeanReversionBot

logger = logging.getLogger(__name__)


class AdaptiveMeanReversionBTCBot(AdaptiveMeanReversionBot):
    """Trend-holding strategy on BTC-USD. The Hyperliquid vault strategy."""

    param_grid = {
        # How calm must volatility be for entry?  Higher = more permissive.
        "atr_multiplier": [1.5, 2.0, 3.0, 5.0],
        # How far below SMA-200 before exiting?  Crypto needs a much wider band
        # than the parent's 0.03-0.20 equity range.
        "sell_buffer": [0.05, 0.08, 0.12, 0.20, 0.30],
    }

    def __init__(
        self,
        atr_multiplier: float = 2.0,
        sell_buffer: float = 0.15,
        **kwargs,
    ):
        super().__init__(
            atr_multiplier=atr_multiplier,
            sell_buffer=sell_buffer,
            name="AdaptiveMeanReversionBTCBot",
            symbol="BTC-USD",
            # 2y so the 200-day SMA has a full warmup plus a usable history.
            period="2y",
            **kwargs,
        )


if __name__ == "__main__":
    bot = AdaptiveMeanReversionBTCBot()
    # bot.local_optimize()   # run this first, then paste the footer below
    bot.run()
