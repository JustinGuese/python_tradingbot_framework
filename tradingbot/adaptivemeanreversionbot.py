"""
Adaptive Mean Reversion Bot (Pattern A)

Enters QQQ on deep Williams %R oversold dips, then holds until the
200-day SMA trend is genuinely broken (not just briefly touched).

Signal logic:
  BUY  : WR < wr_threshold
         AND close > SMA-200   (uptrend intact)
         AND ATR < atr_multiplier × ATR-MA20   (not panic selling)
         AND NOT squeeze   (BBW not at 20-day low)
  SELL : close < SMA-200 × (1 - sell_buffer)
         — only exits on GENUINE breakdown, not brief dips through SMA

Why sell_buffer matters: "close < SMA-200" alone whipsaws in volatile
bull markets — price dips just below the average then recovers. A 3-5%
buffer below SMA-200 requires a confirmed breakdown before exiting,
keeping the position through corrections that don't destroy the trend.

Why no RSI exit: RSI > 75 fires within 1-2 weeks of a WR < -80 entry
(they're inversely correlated on the same 14-period window). That caused
8 quick round-trips capturing only the initial snap-back, missing the
continued trend. Holding until SMA breakdown drastically reduces trade
count and increases time-in-market.

Expected behavior:
  Bull year : enters on first dip, holds all year, return ≈ B&H from entry
  Bear year : exits when SMA breakdown confirmed, avoids sustained drawdown

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
BBW_SQUEEZE_WINDOW = 20


class AdaptiveMeanReversionBot(Bot):
    """
    Mean-reversion entry, trend-held exit on QQQ (Pattern A).

    Buys on deep WR oversold dips in an uptrend.
    Only sells when price is clearly and sustainably below SMA-200.
    Designed to stay invested through normal corrections while protecting
    against genuine bear markets.
    """

    param_grid = {
        "wr_threshold": [-60, -70, -80, -90],
        "atr_multiplier": [1.5, 2.0, 3.0],
        # Exit buffer: how far BELOW SMA-200 before selling.
        # 0.05 = must be 5% below SMA-200. Prevents stop-outs on normal corrections.
        # Wider → fewer exits, more time invested, approaches B&H in bull markets.
        "sell_buffer": [0.03, 0.05, 0.08, 0.12, 0.20],
    }

    def __init__(
        self,
        wr_threshold: float = -60.0,
        atr_multiplier: float = 1.5,
        sell_buffer: float = 0.05,
        **kwargs,
    ):
        """
        Args:
            wr_threshold:  Williams %R threshold for oversold entry (default: -60).
                           Less negative = more frequent entries, earlier in the year.
            atr_multiplier: Reject entry when ATR > multiplier × ATR-MA20 (default: 1.5).
            sell_buffer:   Exit only when close < SMA-200 × (1 - sell_buffer) (default: 0.05 = 5%).
                           Higher = fewer exits, more time invested.
                           5% buffer: weathers normal corrections without exiting.
                           12-20% buffer: only exits in confirmed bear markets.
        """
        super().__init__(
            "AdaptiveMeanReversionBot",
            "QQQ",
            wr_threshold=wr_threshold,
            atr_multiplier=atr_multiplier,
            sell_buffer=sell_buffer,
            **kwargs,
        )
        self.wr_threshold = wr_threshold
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
        """Fetch standard TA data, then append the custom columns used by decisionFunction."""
        data = super().getYFDataWithTA(interval=interval, period=period, saveToDB=saveToDB)
        return self._enrich(data)

    @staticmethod
    def _enrich(data: pd.DataFrame) -> pd.DataFrame:
        """Add computed columns required by decisionFunction."""
        data = data.copy()
        # 200-day SMA — trend filter and exit reference
        data["sma_200"] = data["close"].rolling(SMA_PERIOD, min_periods=1).mean()
        # 20-period ATR rolling mean — volatility clustering baseline
        data["atr_ma20"] = data["volatility_atr"].rolling(ATR_MA_PERIOD, min_periods=1).mean()
        # 20-day rolling minimum of Bollinger Band Width — squeeze detector
        data["bbw_20d_min"] = data["volatility_bbw"].rolling(BBW_SQUEEZE_WINDOW, min_periods=1).min()
        # SMA-200 cross-up — re-entry after a correction stop-out.
        # Uses shift fill_value to avoid FutureWarning from fillna on bool arrays.
        above = data["close"] > data["sma_200"]
        data["sma_cross_up"] = above & ~above.shift(1, fill_value=False)
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
        Single-bar decision.

        Returns:
             1  BUY  — deep WR oversold dip in confirmed uptrend
            -1  SELL — close is clearly below SMA-200 (genuine breakdown)
             0  HOLD — maintain current position
        """
        close = float(row.get("close", 0.0))
        wr = float(row.get("momentum_wr", 0.0))
        sma_200 = float(row.get("sma_200", 0.0))
        atr = float(row.get("volatility_atr", 0.0))
        atr_ma = float(row.get("atr_ma20", 0.0))
        bbw = float(row.get("volatility_bbw", 0.0))
        bbw_min = float(row.get("bbw_20d_min", 0.0))
        sma_cross_up = bool(row.get("sma_cross_up", False))

        # Guard: skip warmup rows with insufficient data
        if not (close > 0 and sma_200 > 0 and not math.isnan(sma_200)
                and atr_ma > 0 and not math.isnan(atr_ma)):
            return 0

        # --- SELL: genuine trend breakdown, buffered to prevent whipsaw ---
        # With sell_buffer=0.05: price must be 5% BELOW SMA-200 before exit.
        # This weathers normal corrections. The gap between exit (SMA × 0.95)
        # and re-entry (above SMA) prevents the whipsaw seen with sell_buffer=0.02.
        if close < sma_200 * (1.0 - self.sell_buffer):
            return -1

        # --- Entry gates (close is above the sell floor) ---------------
        # Volatility gate: avoid entries during ATR acceleration
        if atr > self.atr_multiplier * atr_ma:
            return 0

        # Squeeze filter: breakout imminent, skip mean-reversion entries
        if bbw_min > 0 and bbw <= bbw_min:
            return 0

        # --- BUY signals -----------------------------------------------
        # Signal 1: deep oversold dip while above SMA-200
        if wr < self.wr_threshold and close > sma_200:
            return 1

        # Signal 2: price just recovered above SMA-200 — re-entry after stop-out.
        # Works without whipsaw because the sell floor is sell_buffer% below SMA-200,
        # creating a clear separation between exit and re-entry levels.
        if sma_cross_up:
            return 1

        return 0


bot = AdaptiveMeanReversionBot()
bot.local_development()

# ============================================================
# 2026-03-22 11:39:59 - utils.botclass - INFO - Backtesting with best parameters...
# 2026-03-22 11:39:59 - utils.botclass - INFO - ============================================================
# 2026-03-22 11:39:59 - utils.botclass - INFO -   wr_threshold: -60
# 2026-03-22 11:39:59 - utils.botclass - INFO -   atr_multiplier: 1.5
# 2026-03-22 11:39:59 - utils.botclass - INFO -   sell_buffer: 0.02


# 2026-03-22 11:40:05 - utils.botclass - INFO - 
# --- Backtest Results: AdaptiveMeanReversionBot ---
# 2026-03-22 11:40:05 - utils.botclass - INFO - Yearly Return: 7.99%
# 2026-03-22 11:40:05 - utils.botclass - INFO - Buy & Hold Return: 21.67%
# 2026-03-22 11:40:05 - utils.botclass - INFO - Outperformance vs B&H: -13.68%
# 2026-03-22 11:40:05 - utils.botclass - INFO - Sharpe Ratio: 0.59
# 2026-03-22 11:40:05 - utils.botclass - INFO - Number of Trades: 2
# 2026-03-22 11:40:05 - utils.botclass - INFO - Max Drawdown: 7.88%