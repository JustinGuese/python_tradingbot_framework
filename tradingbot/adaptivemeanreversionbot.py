"""
Adaptive Mean Reversion Bot (Pattern A)

Trend-riding strategy on QQQ: buys whenever the 200-day SMA uptrend is
intact and volatility is calm, holds until the trend is genuinely broken.

Signal logic:
  BUY  : close > SMA-200  AND  ATR < atr_multiplier × ATR-MA20
         (enter at the first opportunity in an uptrend, no dip required)
  SELL : close < SMA-200 × (1 - sell_buffer)
         (only exit on a confirmed breakdown — not a brief correction)

Why no Williams %R entry gate:
  Requiring WR < -60 as a buy trigger means waiting for a significant dip
  before entering. In a strong bull market QQQ may spend weeks at WR > -60,
  keeping the bot in cash for the entire up-move. Removing the WR gate
  means we enter on the first post-warmup bar in an uptrend and immediately
  participate in the trend, matching B&H behaviour in bull years.

Why sell_buffer matters:
  "close < SMA-200" without a buffer fires during every brief correction,
  kicking us out and missing the recovery. A 5-12% buffer requires a
  genuine trend breakdown before exiting, so normal corrections are ridden
  through rather than sold into. Wider buffer → more time invested → higher
  return in bull years; narrower → tighter protection in bear markets.

Expected behaviour:
  Bull year  : enters on bar ~27 (first post-warmup), holds the full run
               → return ≈ B&H (can slightly beat if SMA200 is well below)
  Bear year  : exits on confirmed SMA breakdown, avoids the sustained fall
               → outperforms B&H

Backtestable via local_backtest().
Tune via local_optimize() with param_grid.

Schedule: daily 0 21 * * 1-5  (9 PM UTC = 4 PM ET, near NYSE close)

Research:
  docs/examples/Strategic-Synthesis-of-Adaptive-Mean-Reversion-and-Multi-Asset-Rotation.md
"""

import logging
import math

import pandas as pd

from utils.botclass import Bot

logger = logging.getLogger(__name__)

SMA_PERIOD = 200
ATR_MA_PERIOD = 20


class AdaptiveMeanReversionBot(Bot):
    """
    Trend-holding strategy on QQQ (Pattern A).

    Stays invested whenever the 200-day SMA uptrend is intact and the ATR
    environment is calm. Only exits when price falls well below the SMA-200,
    signalling a genuine downtrend rather than a normal correction.

    Entering on the first qualifying bar (not waiting for a dip) maximises
    time-in-market and closely tracks buy-and-hold during bull years, while
    the buffered sell exit protects against sustained bear markets.
    """

    param_grid = {
        # How calm must volatility be for entry?  Higher = more permissive.
        "atr_multiplier": [1.5, 2.0, 3.0, 5.0],
        # How far below SMA-200 before exiting?  Wider = more time invested.
        "sell_buffer": [0.03, 0.05, 0.08, 0.12, 0.20],
    }

    def __init__(
        self,
        atr_multiplier: float = 1.5,
        sell_buffer: float = 0.03,
        **kwargs,
    ):
        """
        Args:
            atr_multiplier: Reject entry when ATR > multiplier × ATR-MA20 (default: 2.0).
                            Higher values allow entering even during moderately volatile dips.
            sell_buffer:    Exit only when close < SMA-200 × (1 - sell_buffer) (default: 0.08).
                            0.08 = must be 8% below SMA-200. Weathers corrections up to ~8%.
                            Use 0.12-0.20 for near-maximum time-in-market (bear-market-only exits).
        """
        super().__init__(
            "AdaptiveMeanReversionBot",
            "QQQ",
            atr_multiplier=atr_multiplier,
            sell_buffer=sell_buffer,
            **kwargs,
        )
        self.atr_multiplier = atr_multiplier
        self.sell_buffer = sell_buffer
        self.interval = "1d"
        self.period = "1y"

    # ------------------------------------------------------------------
    # Data preparation
    # ------------------------------------------------------------------

    def getYFDataWithTA(
        self,
        interval: str = "1d",
        period: str = "1y",
        saveToDB: bool = True,
    ) -> pd.DataFrame:
        """Fetch standard TA data, then add the custom columns decisionFunction needs."""
        data = super().getYFDataWithTA(interval=interval, period=period, saveToDB=saveToDB)
        return self._enrich(data)

    @staticmethod
    def _enrich(data: pd.DataFrame) -> pd.DataFrame:
        """Add the two computed columns required by decisionFunction."""
        data = data.copy()
        # 200-day SMA — uptrend filter and exit reference
        data["sma_200"] = data["close"].rolling(SMA_PERIOD, min_periods=1).mean()
        # 20-period ATR rolling mean — volatility environment baseline
        data["atr_ma20"] = data["volatility_atr"].rolling(ATR_MA_PERIOD, min_periods=1).mean()
        return data

    # ------------------------------------------------------------------
    # Live execution
    # ------------------------------------------------------------------

    def makeOneIteration(self) -> int:
        self.dbBot = self._bot_repository.create_or_get_bot(self.bot_name)
        data = self.getYFDataWithTA(interval=self.interval, period=self.period, saveToDB=True)
        self.data = data
        self.datasettings = (self.interval, self.period)

        decision = self.getLatestDecision(data)
        cash = self.dbBot.portfolio.get("USD", 0)
        holding = self.dbBot.portfolio.get(self.symbol, 0)

        if decision == 1 and cash > 0:
            self.buy(self.symbol)
            return 1
        if decision == -1 and holding > 0:
            self.sell(self.symbol)
            return -1
        return 0

    # ------------------------------------------------------------------
    # Signal (backtestable)
    # ------------------------------------------------------------------

    def decisionFunction(self, row) -> int:
        """
        Single-bar trend-holding decision.

        Returns:
             1  BUY  — trend intact (close > SMA-200), calm volatility
                       Re-entry fires immediately when SMA-200 is recovered.
                       Already-invested bars also return 1 — backtest ignores
                       duplicate buy signals, so this acts as "maintain long".
            -1  SELL — confirmed breakdown (close < SMA-200 × (1 - sell_buffer))
             0  HOLD — price in the buffer zone (below SMA but above exit level)
                       or ATR is too high for entry
        """
        close = float(row.get("close", 0.0))
        sma_200 = float(row.get("sma_200", 0.0))
        atr = float(row.get("volatility_atr", 0.0))
        atr_ma = float(row.get("atr_ma20", 0.0))

        # Guard: skip warmup rows with insufficient data
        if not (close > 0 and sma_200 > 0 and not math.isnan(sma_200)
                and atr_ma > 0 and not math.isnan(atr_ma)):
            return 0

        # --- SELL: price has broken down well below trend ---------------
        # The sell_buffer gap between exit and re-entry prevents whipsawing:
        #   exit  fires at SMA-200 × (1 − buffer)   [e.g. × 0.92 for 8%]
        #   entry fires at SMA-200                   [always higher than exit]
        if close < sma_200 * (1.0 - self.sell_buffer):
            return -1

        # --- BUY: trend intact AND volatility calm ----------------------
        # Returning 1 when already invested is safe: the backtest engine
        # ignores buy signals when there is no cash to deploy.
        if close > sma_200 and atr <= self.atr_multiplier * atr_ma:
            return 1

        # Neutral zone: between the sell floor and SMA-200 (brief dip),
        # or ATR too high — hold current position unchanged.
        return 0


bot = AdaptiveMeanReversionBot()
# bot.local_development()
bot.run()
# ============================================================
# 2026-03-22 11:51:07 - utils.botclass - INFO - Backtesting with best parameters...
# 2026-03-22 11:51:07 - utils.botclass - INFO - ============================================================
# 2026-03-22 11:51:07 - utils.botclass - INFO -   atr_multiplier: 1.5
# 2026-03-22 11:51:07 - utils.botclass - INFO -   sell_buffer: 0.03

# 2026-03-22 11:51:13 - utils.botclass - INFO - 
# --- Backtest Results: AdaptiveMeanReversionBot ---
# 2026-03-22 11:51:13 - utils.botclass - INFO - Yearly Return: 25.25%
# 2026-03-22 11:51:13 - utils.botclass - INFO - Buy & Hold Return: 21.67%
# 2026-03-22 11:51:13 - utils.botclass - INFO - Outperformance vs B&H: +3.59%
# 2026-03-22 11:51:13 - utils.botclass - INFO - Sharpe Ratio: 1.40
# 2026-03-22 11:51:13 - utils.botclass - INFO - Number of Trades: 2
# 2026-03-22 11:51:13 - utils.botclass - INFO - Max Drawdown: 7.88%