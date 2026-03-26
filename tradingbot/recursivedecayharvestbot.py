"""
Recursive Decay Harvest Bot (Diamond-Hysteresis Edition)

Optimized for TQQQ.
Uses a wide hysteresis gap between panic-exits and calm-entries to avoid 
whipsaws in trending bull markets.
"""

import logging
import math

import numpy as np
import pandas as pd

from utils.core import Bot

logger = logging.getLogger(__name__)

UVXY_RSI_PERIOD = 14


def _compute_rsi(series: pd.Series, period: int = UVXY_RSI_PERIOD) -> pd.Series:
    """Wilder's smoothed RSI."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)


class RecursiveDecayHarvestBot(Bot):
    """
    TQQQ regime-switching strategy with wide hysteresis filters.
    """

    param_grid = {
        "rsi_exit": [75, 80, 85],
        "sma_period": [50, 100, 200],
        "rsi_entry_gap": [15, 20, 25],
    }

    def __init__(
        self,
        rsi_exit: float = 80.0,
        sma_period: int = 100,
        rsi_entry_gap: float = 20.0,
        **kwargs,
    ):
        """
        Args:
            rsi_exit: Exit immediately if UVXY RSI exceeds this (Panic).
            sma_period: Period for the trend-following SMA.
            rsi_entry_gap: How much UVXY RSI must drop below rsi_exit to re-enter.
        """
        super().__init__(
            "RecursiveDecayHarvestBot",
            "TQQQ",
            rsi_exit=rsi_exit,
            sma_period=sma_period,
            rsi_entry_gap=rsi_entry_gap,
            **kwargs,
        )
        self.rsi_exit = rsi_exit
        self.sma_period = int(sma_period)
        self.rsi_entry_gap = rsi_entry_gap
        self.interval = "1d"
        self.period = "2y"

    def getYFDataWithTA(self, interval="1d", period="2y", saveToDB=True, **kwargs) -> pd.DataFrame:
        tqqq_data = super().getYFDataWithTA(interval=interval, period=period, saveToDB=saveToDB, **kwargs)
        aux = self.getYFDataMultiple(symbols=["QQQ", "UVXY"], interval=interval, period=period, saveToDB=saveToDB)

        if aux.empty:
            return tqqq_data

        return self._enrich(tqqq_data, aux)

    def _enrich(self, tqqq_df: pd.DataFrame, aux_df: pd.DataFrame) -> pd.DataFrame:
        result = tqqq_df.copy()
        result["_date"] = pd.to_datetime(result["timestamp"]).dt.date

        # QQQ: Trend Filter
        qqq = aux_df[aux_df["symbol"] == "QQQ"].copy().sort_values("timestamp")
        if not qqq.empty:
            qqq["_date"] = pd.to_datetime(qqq["timestamp"]).dt.date
            qqq["qqq_close"] = qqq["close"]
            qqq["qqq_sma"] = qqq["close"].rolling(self.sma_period).mean()
            result = result.merge(qqq[["_date", "qqq_close", "qqq_sma"]], on="_date", how="left")
            result = result.ffill().fillna(0.0)

        # UVXY: Panic Filter
        uvxy = aux_df[aux_df["symbol"] == "UVXY"].copy().sort_values("timestamp")
        if not uvxy.empty:
            uvxy["_date"] = pd.to_datetime(uvxy["timestamp"]).dt.date
            uvxy["uvxy_rsi"] = _compute_rsi(uvxy["close"])
            result = result.merge(uvxy[["_date", "uvxy_rsi"]], on="_date", how="left")
            result["uvxy_rsi"] = result["uvxy_rsi"].ffill().fillna(50.0)

        return result.drop(columns=["_date"], errors="ignore")

    def decisionFunction(self, row) -> int:
        qqq_close = float(row.get("qqq_close", 0.0))
        qqq_sma = float(row.get("qqq_sma", 0.0))
        uvxy_rsi = float(row.get("uvxy_rsi", 50.0))

        if qqq_close <= 0 or qqq_sma <= 0:
            return 0

        # --- EXIT LOGIC (Panic or Trend Break) ---
        
        # 1. Extreme Volatility Panic
        if uvxy_rsi > self.rsi_exit:
            return -1
            
        # 2. Clear Trend Breakdown (2% buffer below SMA)
        if qqq_close < qqq_sma * 0.98:
            return -1

        # --- ENTRY LOGIC (Hysteresis Gap) ---
        
        # Bullish Regime AND Volatility has settled significantly
        # The 'gap' ensures we don't buy during a volatile bounce
        if qqq_close > qqq_sma:
            if uvxy_rsi < (self.rsi_exit - self.rsi_entry_gap):
                return 1

        # Otherwise: Hold existing position (Cash or TQQQ)
        return 0


bot = RecursiveDecayHarvestBot()
# bot.local_development()
bot.run()