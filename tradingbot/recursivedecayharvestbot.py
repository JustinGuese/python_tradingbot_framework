"""
Recursive Decay Harvest Bot

TQQQ regime-switching strategy based on the "Recursive Adversarial Arbitrage"
research: leveraged ETF volatility-decay theory + multi-signal crash filter.

The core insight: 3x leveraged ETFs suffer mathematical erosion (beta slippage)
in choppy or volatile regimes. By holding TQQQ only during confirmed QQQ uptrends
with calm volatility — and exiting to cash when the crash filter fires — the bot
captures 3x upside in bull markets while avoiding the catastrophic drawdowns that
destroy buy-and-hold holders of leveraged ETFs.

Signal logic:
  BUY  : QQQ > QQQ_SMA200           (trend intact)
         AND UVXY_RSI < uvxy_rsi_exit  (volatility not spiking)
  SELL : UVXY_RSI > uvxy_rsi_exit     (crash filter: fear / volatility panic)
         OR  QQQ  < QQQ_SMA200 × (1 − sell_buffer)  (buffered trend breakdown)
  HOLD : QQQ between SMA200 and exit floor — brief dip, hold position unchanged

Why UVXY RSI:
  UVXY tracks 2x leveraged VIX futures. When its RSI spikes above ~65, the
  volatility regime has shifted from calm to fearful — exactly the environment
  where TQQQ loses value fastest from both the market drop AND amplified beta
  slippage. Exiting before the fear spike completes is the key edge.

Why sell_buffer:
  A bare "QQQ < SMA200" exit fires on every brief correction and whipsaws out
  of good trends. A 5-8% buffer requires a genuine regime breakdown, so normal
  dips are ridden through rather than sold into.

Expected behaviour:
  Bull year (QQQ up ~20%) : enters near bar ~27, holds ~all year → TQQQ ~50-60%
  Bear / volatile year     : UVXY RSI fires early, exits well before max drawdown

Instruments:
  Primary  : TQQQ  (3x Nasdaq-100 long, ProShares)
  Auxiliary: QQQ   (trend filter — 200-day SMA)
             UVXY  (crash filter  — RSI of 2x VIX futures ETF)

Backtestable via local_backtest().
Tune via local_optimize() with param_grid.

Schedule: daily 45 21 * * 1-5  (9:45 PM UTC = 4:45 PM ET, after NYSE close)

Research:
  docs/examples/Recursive-Adversarial-Arbitrage.md
"""

import logging
import math

import numpy as np
import pandas as pd

from utils.botclass import Bot

logger = logging.getLogger(__name__)

QQQ_SMA_PERIOD = 200
UVXY_RSI_PERIOD = 14


def _compute_rsi(series: pd.Series, period: int = UVXY_RSI_PERIOD) -> pd.Series:
    """Wilder's smoothed RSI (same formula as pandas_ta / TA-Lib)."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)


class RecursiveDecayHarvestBot(Bot):
    """
    TQQQ regime-switching strategy with dual crash filter (Pattern A).

    Exploits leveraged ETF beta-slippage avoidance: holds TQQQ aggressively
    during confirmed QQQ uptrends with calm UVXY, exits to cash the moment the
    crash filter (UVXY RSI spike or QQQ SMA breakdown) triggers.

    Entering on the first qualifying bar (near bar ~27) maximises time-in-market
    during bull runs. The UVXY RSI gate adds a second layer of exit protection
    that fires before the price breakdown — often exiting 1-5 days earlier than
    a pure SMA-based filter.
    """

    param_grid = {
        # Exit immediately when UVXY RSI exceeds this: lower = more defensive
        "uvxy_rsi_exit": [55, 60, 65, 70,75,80,85,100],
        # Exit when QQQ falls this far below SMA200: wider = fewer SMA exits
        "sell_buffer": [0,0.005,0.01,0.02, 0.05, 0.08, 0.12],
    }

    def __init__(
        self,
        uvxy_rsi_exit: float = 70.0,
        sell_buffer: float = 0.02,
        **kwargs,
    ):
        """
        Args:
            uvxy_rsi_exit: Sell when UVXY 14-period RSI exceeds this (default: 65).
                           55–60 = aggressive defence, exits early on any fear spike.
                           65–70 = moderate defence, only exits on confirmed panic.
            sell_buffer:   Sell when QQQ < QQQ_SMA200 × (1 − sell_buffer) (default: 0.05).
                           0.02 = hair-trigger (exits on any dip below SMA).
                           0.08–0.12 = durable (exits only on genuine bear market).
        """
        super().__init__(
            "RecursiveDecayHarvestBot",
            "TQQQ",
            uvxy_rsi_exit=uvxy_rsi_exit,
            sell_buffer=sell_buffer,
            **kwargs,
        )
        self.uvxy_rsi_exit = uvxy_rsi_exit
        self.sell_buffer = sell_buffer
        self.interval = "1d"
        self.period = "2y"  # 2 years: solid SMA200 warmup + ~252 live bars

    # ------------------------------------------------------------------
    # Data preparation
    # ------------------------------------------------------------------

    def getYFDataWithTA(
        self,
        interval: str = "1d",
        period: str = "2y",
        saveToDB: bool = True,
    ) -> pd.DataFrame:
        """Fetch TQQQ OHLCV+TA, then overlay QQQ SMA200 and UVXY RSI columns."""
        tqqq_data = super().getYFDataWithTA(interval=interval, period=period, saveToDB=saveToDB)

        aux = self.getYFDataMultiple(
            symbols=["QQQ", "UVXY"],
            interval=interval,
            period=period,
            saveToDB=saveToDB,
        )

        if aux.empty:
            logger.warning("QQQ/UVXY data unavailable — falling back to TQQQ-only proxy")
            tqqq_data["qqq_close"] = tqqq_data["close"]
            tqqq_data["qqq_sma200"] = tqqq_data["close"].rolling(QQQ_SMA_PERIOD, min_periods=1).mean()
            tqqq_data["uvxy_rsi"] = 50.0
            return tqqq_data

        return self._enrich(tqqq_data, aux)

    @staticmethod
    def _enrich(tqqq_df: pd.DataFrame, aux_df: pd.DataFrame) -> pd.DataFrame:
        """Merge QQQ (close + SMA200) and UVXY RSI onto TQQQ rows by calendar date."""
        result = tqqq_df.copy()
        result["_date"] = pd.to_datetime(result["timestamp"]).dt.date

        # --- QQQ: trend reference columns --------------------------------
        qqq = aux_df[aux_df["symbol"] == "QQQ"].copy()
        if not qqq.empty:
            qqq = qqq.sort_values("timestamp").reset_index(drop=True)
            qqq["_date"] = pd.to_datetime(qqq["timestamp"]).dt.date
            qqq["qqq_close"] = qqq["close"]
            qqq["qqq_sma200"] = qqq["close"].rolling(QQQ_SMA_PERIOD, min_periods=1).mean()
            result = result.merge(
                qqq[["_date", "qqq_close", "qqq_sma200"]], on="_date", how="left"
            )
            result["qqq_close"] = result["qqq_close"].ffill().fillna(0.0)
            result["qqq_sma200"] = result["qqq_sma200"].ffill().fillna(0.0)
        else:
            logger.warning("QQQ data missing — using TQQQ close as QQQ proxy")
            result["qqq_close"] = result["close"]
            result["qqq_sma200"] = result["close"].rolling(QQQ_SMA_PERIOD, min_periods=1).mean()

        # --- UVXY: volatility crash filter --------------------------------
        uvxy = aux_df[aux_df["symbol"] == "UVXY"].copy()
        if not uvxy.empty:
            uvxy = uvxy.sort_values("timestamp").reset_index(drop=True)
            uvxy["_date"] = pd.to_datetime(uvxy["timestamp"]).dt.date
            uvxy["uvxy_rsi"] = _compute_rsi(uvxy["close"])
            result = result.merge(uvxy[["_date", "uvxy_rsi"]], on="_date", how="left")
            result["uvxy_rsi"] = result["uvxy_rsi"].ffill().fillna(50.0)
        else:
            logger.warning("UVXY data missing — volatility filter disabled (RSI neutral=50)")
            result["uvxy_rsi"] = 50.0

        return result.drop(columns=["_date"], errors="ignore")

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
        TQQQ regime-switching decision.

        Returns:
             1  BUY  — QQQ uptrend intact AND UVXY calm → enter/hold TQQQ
                       Already-invested bars also return 1; backtest ignores
                       duplicate buys (acts as "maintain long").
            -1  SELL — crash filter fired: UVXY panic OR QQQ trend broken
             0  HOLD — QQQ in buffer zone (brief dip, not a confirmed breakdown)
                       or warmup row with incomplete data
        """
        qqq_close = float(row.get("qqq_close", 0.0))
        qqq_sma200 = float(row.get("qqq_sma200", 0.0))
        uvxy_rsi = float(row.get("uvxy_rsi", 50.0))

        # Guard: skip warmup rows or rows with missing auxiliary data
        if not (qqq_close > 0 and qqq_sma200 > 0 and not math.isnan(qqq_sma200)):
            return 0

        # --- SELL: crash filter — volatility panic -----------------------
        # UVXY RSI spike signals institutional fear; leveraged ETF decay
        # accelerates quadratically with volatility — exit before full unwind
        if uvxy_rsi > self.uvxy_rsi_exit:
            return -1

        # --- SELL: QQQ trend breakdown with buffer -----------------------
        # Buffer zone between exit (SMA × (1-buf)) and re-entry (SMA) prevents
        # whipsawing: exit fires well below SMA, re-entry fires above SMA
        if qqq_close < qqq_sma200 * (1.0 - self.sell_buffer):
            return -1

        # --- BUY: confirmed uptrend + calm volatility -------------------
        if qqq_close > qqq_sma200 and uvxy_rsi <= self.uvxy_rsi_exit:
            return 1

        # Neutral zone: QQQ between exit floor and SMA200 — hold unchanged
        return 0


bot = RecursiveDecayHarvestBot()
# bot.local_development()
bot.run()
# 026-03-22 12:03:27 - utils.botclass - INFO - Backtesting with best parameters...
# 2026-03-22 12:03:27 - utils.botclass - INFO - ============================================================
# 2026-03-22 12:03:27 - utils.botclass - INFO -   uvxy_rsi_exit: 70
# 2026-03-22 12:03:27 - utils.botclass - INFO -   sell_buffer: 0.02

# --- Backtest Results: RecursiveDecayHarvestBot ---
# 2026-03-22 12:03:38 - utils.botclass - INFO - Yearly Return: 76.14%
# 2026-03-22 12:03:38 - utils.botclass - INFO - Buy & Hold Return: 39.78%
# 2026-03-22 12:03:38 - utils.botclass - INFO - Outperformance vs B&H: +36.36%
# 2026-03-22 12:03:38 - utils.botclass - INFO - Sharpe Ratio: 1.11
# 2026-03-22 12:03:38 - utils.botclass - INFO - Number of Trades: 2
# 2026-03-22 12:03:38 - utils.botclass - INFO - Max Drawdown: 22.97%