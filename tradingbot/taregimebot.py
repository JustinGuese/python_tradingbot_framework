"""
TA-only regime-adaptive bot: historic data only (no Fear & Greed).

Regime and signal logic live in utils.ta_regime; this bot only fetches data
and calls ta_regime_decision.
"""

from utils.core import Bot
from utils.ta_regime import ta_regime_decision


class TARegimeAdaptiveBot(Bot):
    """
    Single-asset bot that uses a Hurst-style regime (trend vs mean-reversion)
    and TA indicators from historic OHLCV only. All decision logic is in
    utils.ta_regime; the bot delegates decisionFunction to ta_regime_decision.
    """

    # Grid centered around best params (from prior tuning: ~12.58% return, 2.65 Sharpe)
    param_grid = {
        "hurst_window": [40, 50, 60],
        "hurst_trend_threshold": [0.44, 0.46, 0.48],
        "adx_threshold": [14, 16, 18],
        "rsi_oversold": [34, 36, 38],
        "rsi_overbought": [64, 66, 68],
        "bbp_low": [0.0, 0.05, 0.1],
        "bbp_high": [0.8, 0.85, 0.9],
        "zscore_window": [0, 15, 20],
        "zscore_entry": [1.5, 1.75, 2.0],
    }

    def __init__(
        self,
        symbol: str = "SPY",
        interval: str = "1d",
        period: str = "3mo",
        hurst_window: int = 50,
        hurst_trend_threshold: float = 0.46,
        adx_threshold: float = 16,
        rsi_oversold: float = 36,
        rsi_overbought: float = 66,
        bbp_low: float = 0.0,
        bbp_high: float = 0.8,
        zscore_window: int = 15,
        zscore_entry: float = 1.5,
        macd_confirm_trend: bool = True,
        **kwargs,
    ):
        super().__init__(
            "TARegimeAdaptiveBot",
            symbol=symbol,
            interval=interval,
            period=period,
            hurst_window=hurst_window,
            hurst_trend_threshold=hurst_trend_threshold,
            adx_threshold=adx_threshold,
            rsi_oversold=rsi_oversold,
            rsi_overbought=rsi_overbought,
            bbp_low=bbp_low,
            bbp_high=bbp_high,
            zscore_window=zscore_window,
            zscore_entry=zscore_entry,
            macd_confirm_trend=macd_confirm_trend,
            **kwargs,
        )
        self._ta_params = {
            "hurst_window": hurst_window,
            "hurst_trend_threshold": hurst_trend_threshold,
            "adx_threshold": adx_threshold,
            "rsi_oversold": rsi_oversold,
            "rsi_overbought": rsi_overbought,
            "bbp_low": bbp_low,
            "bbp_high": bbp_high,
            "zscore_window": zscore_window,
            "zscore_entry": zscore_entry,
            "macd_confirm_trend": macd_confirm_trend,
        }

    def decisionFunction(self, row):
        return ta_regime_decision(row, self.data, **self._ta_params)

if __name__ == "__main__":
    bot = TARegimeAdaptiveBot()

    # ============================================================
    # Backtesting with best parameters (max-sharpe)
    # ============================================================
    # hurst_window: 50
    # hurst_trend_threshold: 0.46
    # adx_threshold: 16
    # rsi_oversold: 36
    # rsi_overbought: 66
    # bbp_low: 0.0
    # bbp_high: 0.8
    # zscore_window: 15
    # zscore_entry: 1.5

    # --- Backtest Results: TARegimeAdaptiveBot ---
    # Yearly Return: 12.58%
    # Buy & Hold Return: 16.32%
    # Outperformance vs B&H: -3.74%
    # Sharpe Ratio: 2.65
    # Number of Trades: 7
    # Max Drawdown: 2.62%
    # bot.local_development(objective="yearly_return", param_sample_ratio=.1) # 
    bot.run()
    # bot.local_development()