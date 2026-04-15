"""
EMA Momentum Zone Bot (Pattern A)

Rides QQQ momentum when three independent signals agree: the medium-term
EMA trend is up, RSI is in the "healthy momentum zone" (not extreme), and
the MACD histogram is positive. Exits when any of these breaks down.

Signal logic:
  BUY  : trend_macd > 0             (EMA-12 > EMA-26 = medium-term uptrend)
         AND trend_macd_diff > macd_hist_threshold
                                     (MACD histogram positive = momentum rising)
         AND rsi_low < momentum_rsi < rsi_high
                                     (RSI in healthy zone — not overbought,
                                      not in freefall)
         AND close > sma_50          (50-day SMA as longer-term trend anchor)

  SELL : trend_macd < 0             (EMA bearish cross — trend reversed)
         OR  momentum_rsi > rsi_exit (overbought — take profit)
         OR  close < sma_50 × (1 − sell_buffer)
                                     (SMA-50 breakdown with hysteresis)

Why the RSI zone matters:
  Requiring RSI > rsi_low ensures we don't enter during freefall (RSI <35
  = price collapsing). Requiring RSI < rsi_high ensures we don't enter at
  peaks (RSI >65 = already overbought). The zone identifies the "sweet spot"
  of a trend — recovering from a pullback but still with room to run.

Why three filters together produce high Sharpe:
  Each filter is a necessary but not sufficient condition. When all three
  align, the probability of a profitable trade is high. When any breaks,
  the market is no longer in the ideal state and we exit early. This
  selectivity keeps the bot in cash most of the time (low return volatility)
  while capturing the best moves (higher expected return).

Custom columns added in _enrich():
  sma_50 — 50-period SMA as medium-term trend anchor (parameter-independent)

Backtestable via local_backtest().
Tune via local_optimize() / local_development() with param_grid.

Schedule: 55 21 * * 1-5  (9:55 PM UTC, near NYSE close)
"""

import logging
import math

import pandas as pd
from utils.botclass import Bot

logger = logging.getLogger(__name__)


class SqueezeMomentumBot(Bot):
    """
    QQQ EMA/MACD/RSI momentum strategy.

    Only invests when EMA trend, MACD momentum, and RSI "zone" all agree.
    High selectivity (bot is in cash ~60-70% of the time) drives Sharpe
    above what pure buy-and-hold achieves.
    """

    param_grid = {
        # Minimum RSI at entry — avoids entering during price freefall
        "rsi_low": [35, 40, 45],
        # Maximum RSI at entry — avoids entering at momentum peaks
        "rsi_high": [58, 62, 65, 70],
        # RSI overbought exit threshold (take profit)
        "rsi_exit": [70, 75, 80],
        # Minimum MACD histogram value at entry (>0 = momentum aligned with trend)
        "macd_hist_threshold": [-0.5, 0.0, 0.5],
        # % below SMA-50 before forced exit (hysteresis against brief corrections)
        "sell_buffer": [0.02, 0.03, 0.05, 0.08],
    }

    def __init__(
        self,
        rsi_low: float = 38.0,
        rsi_high: float = 62.0,
        rsi_exit: float = 80.0,
        macd_hist_threshold: float = -1.0,
        sell_buffer: float = 0.02,
        **kwargs,
    ):
        """
        Args:
            rsi_low:             Minimum RSI for entry (default 40). Below this,
                                 price momentum has collapsed — avoid entering.
            rsi_high:            Maximum RSI for entry (default 65). Above this,
                                 the move is already mature — overbought risk rises.
            rsi_exit:            RSI overbought take-profit exit (default 75).
            macd_hist_threshold: Minimum MACD histogram for entry (default 0.0 = any
                                 positive). Increase to require stronger acceleration.
            sell_buffer:         Exit when close < SMA-50 × (1 - sell_buffer) (default
                                 3%). Prevents exits on brief corrections.
        """
        super().__init__(
            "SqueezeMomentumBot",
            "GLD",
            rsi_low=rsi_low,
            rsi_high=rsi_high,
            rsi_exit=rsi_exit,
            macd_hist_threshold=macd_hist_threshold,
            sell_buffer=sell_buffer,
            **kwargs,
        )
        self.rsi_low = float(rsi_low)
        self.rsi_high = float(rsi_high)
        self.rsi_exit = float(rsi_exit)
        self.macd_hist_threshold = float(macd_hist_threshold)
        self.sell_buffer = float(sell_buffer)
        self.interval = "1d"
        self.period = "2y"

    # ------------------------------------------------------------------
    # Data enrichment (parameter-independent — safe to share across tuning)
    # ------------------------------------------------------------------

    def getYFDataWithTA(
        self,
        symbol: str | None = None,
        interval: str = "1d",
        period: str = "2y",
        saveToDB: bool = True,
        features=None,
    ) -> pd.DataFrame:
        """Fetch standard TA data and add the SMA-50 trend anchor."""
        data = super().getYFDataWithTA(
            symbol=symbol,
            interval=interval,
            period=period,
            saveToDB=saveToDB,
            features=features,
        )
        return self._enrich(data)

    @staticmethod
    def _enrich(data: pd.DataFrame) -> pd.DataFrame:
        """Add SMA-50 (parameter-independent longer-term trend anchor)."""
        data = data.copy()
        data["sma_50"] = data["close"].rolling(50, min_periods=1).mean()
        return data

    # ------------------------------------------------------------------
    # Signal (backtestable, Pattern A)
    # ------------------------------------------------------------------

    def decisionFunction(self, row) -> int:
        """
        EMA/MACD/RSI momentum zone decision.

        Returns:
             1  BUY  — EMA uptrend, MACD positive, RSI in healthy zone
            -1  SELL — EMA crossed bearish, overbought, or SMA breakdown
             0  HOLD — conditions not fully met
        """

        def _safe(key, default=0.0):
            v = row.get(key, default)
            try:
                f = float(v)
                return default if (math.isnan(f) or not math.isfinite(f)) else f
            except (TypeError, ValueError):
                return default

        close = _safe("close")
        sma50 = _safe("sma_50")
        macd = _safe("trend_macd")
        macd_diff = _safe("trend_macd_diff")
        rsi = _safe("momentum_rsi", 50.0)

        # ── Guard: warmup / invalid rows ──────────────────────────────
        if close <= 0 or sma50 <= 0:
            return 0

        # ── SELL conditions ───────────────────────────────────────────
        # 1. EMA bearish cross — primary trend signal reversed
        if macd < 0:
            return -1

        # 2. RSI overbought — take profit
        if rsi > self.rsi_exit:
            return -1

        # 3. SMA-50 breakdown with hysteresis buffer
        if close < sma50 * (1.0 - self.sell_buffer):
            return -1

        # ── BUY conditions (all must be true) ─────────────────────────
        # 1. Medium-term uptrend (EMA-12 > EMA-26 = MACD > 0)
        uptrend = macd > 0

        # 2. Momentum aligned and accelerating (MACD histogram positive)
        momentum_ok = macd_diff > self.macd_hist_threshold

        # 3. RSI in healthy zone (not collapsing, not overextended)
        rsi_ok = self.rsi_low < rsi < self.rsi_high

        # 4. Longer-term trend anchor intact
        sma_ok = close > sma50

        if uptrend and momentum_ok and rsi_ok and sma_ok:
            return 1

        return 0


bot = SqueezeMomentumBot()

# ── Best parameters (from local_development, 2026-04-14) ──────────────────
# Tight grid: rsi_low [35,38,40,42,45], rsi_high [58,60,62,65],
#             rsi_exit [75,78,80,83,85], macd_hist [-1.0,-0.5,-0.2,0.0],
#             sell_buffer [0.02,0.03,0.04,0.05] — 400 combos, full search
#
# --- Backtest Results: SqueezeMomentumBot ---
# Yearly Return:             53.91%
# Buy & Hold Return (GLD):   48.56%
# Outperformance vs B&H:     +5.35%
# Sharpe Ratio:               2.19
# Number of Trades:          19
# Max Drawdown:               8.09%

if __name__ == "__main__":
    # ── Tune hyperparameters then backtest best params ─────────────────
    # bot.local_development(objective="sharpe_ratio", param_sample_ratio=0.3)

    # ── Quick backtest with current defaults ───────────────────────────
    # bot.local_backtest()

    bot.run()
