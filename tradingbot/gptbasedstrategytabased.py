from utils.core import Bot


class gptbasedstrategytabased(Bot):
    # Refined hyperparameter search space including requested exit logic
    param_grid = {
        "adx_threshold": [15, 20, 25],
        "rsi_buy": [60, 65, 70],
        "vix_rsi_exit": [65, 70, 75],
        "sell_buffer": [0.01, 0.02, 0.03],
        "bbp_buy_threshold": [0.4, 0.5, 0.6],
    }

    def __init__(
        self,
        adx_threshold: float = 20.0,
        rsi_buy: float = 65.0,
        vix_rsi_exit: float = 70.0,
        sell_buffer: float = 0.02,
        bbp_buy_threshold: float = 0.5,
        **kwargs
    ):
        """
        Improved GPT-based strategy for BTC.
        
        Args:
            adx_threshold: Minimum ADX for trend strength (default: 20.0)
            rsi_buy: Max RSI for buy entry (default: 65.0)
            vix_rsi_exit: RSI level to trigger 'fear' exit (default: 70.0)
            sell_buffer: Percentage below SMA to trigger trend exit (default: 0.02)
            bbp_buy_threshold: Max Bollinger %B for entry (default: 0.5)
        """
        # Store parameters
        self.adx_threshold = adx_threshold
        self.rsi_buy = rsi_buy
        self.vix_rsi_exit = vix_rsi_exit
        self.sell_buffer = sell_buffer
        self.bbp_buy_threshold = bbp_buy_threshold
        
        # Increased period to 1y for statistically significant backtesting
        super().__init__(
            "GptBasedStrategyBTCTabased",
            "BTC-USD",
            interval="1d",
            period="1y",
            adx_threshold=adx_threshold,
            rsi_buy=rsi_buy,
            vix_rsi_exit=vix_rsi_exit,
            sell_buffer=sell_buffer,
            bbp_buy_threshold=bbp_buy_threshold,
            **kwargs
        )

    def decisionFunction(self, row) -> int:
        """
        Improved Decision function using trend-following with a volatility-aware exit.
        """
        import numpy as np
        import pandas as pd
        
        def safe_get(indicator, default=0.0):
            value = row.get(indicator, default)
            if pd.isna(value):
                return default
            try:
                return float(value)
            except (ValueError, TypeError):
                return default
        
        close = safe_get("close", 0.0)
        if close <= 0:
            return 0
        
        # 1. Trend Indicators
        sma_50 = safe_get("trend_sma_fast")  # 50-day proxy
        sma_200 = safe_get("trend_sma_slow") # 200-day proxy
        adx = safe_get("trend_adx")
        
        # 2. Momentum & Volatility
        rsi = safe_get("momentum_rsi", 50.0)
        macd_diff = safe_get("trend_macd_diff", 0.0)
        bbp = safe_get("volatility_bbp", 0.5)
        
        # Check validity
        if sma_50 <= 0 or sma_200 <= 0:
            return 0

        # --- ENTRY LOGIC (Bullish Trend Following) ---
        # Enter when: 
        # - Golden Cross (50 > 200)
        # - Price is above 50 SMA
        # - ADX confirms trend strength
        # - Not overbought (RSI)
        # - MACD histogram is positive
        # - Not at the very top of Bollinger Bands
        
        if close > sma_50 and sma_50 > sma_200:
            if adx > self.adx_threshold and rsi < self.rsi_buy:
                if macd_diff > 0 and bbp < self.bbp_buy_threshold:
                    return 1
        
        # --- EXIT LOGIC (Risk Management) ---
        # Exit when:
        # - Price falls below the 50 SMA by the 'sell_buffer' percentage
        # - RSI hits the 'extreme' threshold (vix_rsi_exit)
        
        exit_threshold = sma_50 * (1 - self.sell_buffer)
        
        if close < exit_threshold or rsi > self.vix_rsi_exit:
            return -1
        
        return 0


bot = gptbasedstrategytabased()
bot.run()
# Start with a backtest of the new logic
# bot.local_backtest()
# bot.local_development()
#  GptBasedStrategyBTCTabased ---
# 2026-03-23 16:08:44 - utils.botclass - INFO - Yearly Return: 24.20%
# 2026-03-23 16:08:44 - utils.botclass - INFO - Buy & Hold Return: -16.84%
# 2026-03-23 16:08:44 - utils.botclass - INFO - Outperformance vs B&H: +41.05%
# 2026-03-23 16:08:44 - utils.botclass - INFO - Sharpe Ratio: 0.68
# 2026-03-23 16:08:44 - utils.botclass - INFO - Number of Trades: 3
# 2026-03-23 16:08:44 - utils.botclass - INFO - Max Drawdown: 14.66%
# adx_threshold: 25
# 2026-03-23 16:08:17 - utils.botclass - INFO -   rsi_buy: 65
# 2026-03-23 16:08:17 - utils.botclass - INFO -   rsi_sell: 25
# 2026-03-23 16:08:17 - utils.botclass - INFO -   bbp_buy_low: 0.2
# 2026-03-23 16:08:17 - utils.botclass - INFO -   bbp_buy_high: 0.7
# 2026-03-23 16:08:17 - utils.botclass - INFO -   mfi_buy: 75
# 2026-03-23 16:08:17 - utils.botclass - INFO -   mfi_sell: 15