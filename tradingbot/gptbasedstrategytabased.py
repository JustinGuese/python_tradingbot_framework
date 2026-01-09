from utils.botclass import Bot


class gptbasedstrategytabased(Bot):
    # Define the hyperparameter search space for this bot
    param_grid = {
        "adx_threshold": [15, 20, 25, 30],
        "rsi_buy": [65, 70, 75],
        "rsi_sell": [25, 30, 35],
        "bbp_buy_low": [0.2, 0.3, 0.4],
        "bbp_buy_high": [0.6, 0.7, 0.8],
        "mfi_buy": [75, 80, 85],
        "mfi_sell": [15, 20, 25],
    }
    def __init__(
        self,
        adx_threshold: float = 20.0,
        rsi_buy: float = 65.0,
        rsi_sell: float = 25.0,
        bbp_buy_low: float = 0.2,
        bbp_buy_high: float = 0.7,
        bbp_sell_low: float = 0.2,
        bbp_sell_high: float = 0.7,
        mfi_buy: float = 75.0,
        mfi_sell: float = 15.0,
        **kwargs
    ):
        """
        Initialize the GPT-based strategy bot with configurable hyperparameters.
        
        Args:
            adx_threshold: ADX threshold below which trend is considered weak (default: 20.0)
            rsi_buy: RSI threshold for buy signals - must be below this (default: 70.0)
            rsi_sell: RSI threshold for sell signals - must be above this (default: 30.0)
            bbp_buy_low: Lower Bollinger Band position for buy signals (default: 0.3)
            bbp_buy_high: Upper Bollinger Band position for buy signals (default: 0.7)
            bbp_sell_low: Lower Bollinger Band position for sell signals (default: 0.3)
            bbp_sell_high: Upper Bollinger Band position for sell signals (default: 0.7)
            mfi_buy: MFI threshold for buy signals - must be below this (default: 80.0)
            mfi_sell: MFI threshold for sell signals - must be above this (default: 20.0)
            **kwargs: Additional parameters passed to base class
        """
        # Store parameters as instance variables for easy access
        self.adx_threshold = adx_threshold
        self.rsi_buy = rsi_buy
        self.rsi_sell = rsi_sell
        self.bbp_buy_low = bbp_buy_low
        self.bbp_buy_high = bbp_buy_high
        self.bbp_sell_low = bbp_sell_low
        self.bbp_sell_high = bbp_sell_high
        self.mfi_buy = mfi_buy
        self.mfi_sell = mfi_sell
        
        # Pass parameters to base class via kwargs
        super().__init__(
            "GptBasedStrategyBTCTabased",
            "BTC-USD",
            interval="1d",
            period="1mo",
            adx_threshold=adx_threshold,
            rsi_buy=rsi_buy,
            rsi_sell=rsi_sell,
            bbp_buy_low=bbp_buy_low,
            bbp_buy_high=bbp_buy_high,
            bbp_sell_low=bbp_sell_low,
            bbp_sell_high=bbp_sell_high,
            mfi_buy=mfi_buy,
            mfi_sell=mfi_sell,
            **kwargs
        )

    def decisionFunction(self, row) -> int:
        """
        Decision function for Bitcoin trading using multiple technical indicators.
        
        Uses trend, momentum, volatility, and volume indicators to generate
        buy (1), sell (-1), or hold (0) signals optimized for BTC daily trading.
        
        Args:
            row: Pandas Series with market data and technical indicators
            
        Returns:
            -1: Sell signal
             0: Hold (no action)
             1: Buy signal
        """
        import numpy as np
        import pandas as pd
        
        # Helper function to safely get indicator value with NaN handling
        def safe_get(indicator, default=0.0):
            value = row.get(indicator, default)
            if pd.isna(value):
                return default
            try:
                return float(value)
            except (ValueError, TypeError):
                return default
        
        # Check if we have valid price data
        close_price = safe_get("close", 0.0)
        if close_price <= 0:
            return 0  # Invalid price data, hold
        
        # Extract indicator values with NaN handling
        ema_fast = safe_get("trend_ema_fast", 0.0)
        ema_slow = safe_get("trend_ema_slow", 0.0)
        sma_fast = safe_get("trend_sma_fast", 0.0)
        sma_slow = safe_get("trend_sma_slow", 0.0)
        adx = safe_get("trend_adx", 0.0)
        rsi = safe_get("momentum_rsi", 50.0)
        macd = safe_get("trend_macd", 0.0)
        macd_signal = safe_get("trend_macd_signal", 0.0)
        macd_diff = safe_get("trend_macd_diff", 0.0)
        bbp = safe_get("volatility_bbp", 0.5)
        mfi = safe_get("volume_mfi", 50.0)
        
        # Validate RSI is in reasonable range (0-100)
        if not (0 <= rsi <= 100):
            rsi = 50.0  # Default to neutral if invalid
        
        # Validate MFI is in reasonable range (0-100)
        if not (0 <= mfi <= 100):
            mfi = 50.0  # Default to neutral if invalid
        
        # Validate BBP is in reasonable range (0-1)
        if not (0 <= bbp <= 1):
            bbp = 0.5  # Default to middle if invalid
        
        # If ADX is too low, trend is weak - hold
        if adx <= self.adx_threshold:
            return 0
        
        # Check if we have valid moving average values (must be positive and reasonable)
        if (ema_fast <= 0 or ema_slow <= 0 or sma_fast <= 0 or sma_slow <= 0 or
            not np.isfinite(ema_fast) or not np.isfinite(ema_slow) or
            not np.isfinite(sma_fast) or not np.isfinite(sma_slow)):
            return 0
        
        # Determine trend direction
        uptrend = (ema_fast > ema_slow) and (sma_fast > sma_slow)
        downtrend = (ema_fast < ema_slow) and (sma_fast < sma_slow)
        
        # Buy Signal Conditions
        if uptrend:
            # Momentum conditions for buy: RSI not overbought
            rsi_buy = rsi < self.rsi_buy
            macd_buy = (macd > macd_signal) or (macd_diff > 0)  # MACD bullish
            volatility_buy = (bbp < self.bbp_buy_low) or (self.bbp_buy_low <= bbp <= self.bbp_buy_high)  # Near lower BB or middle range
            
            # Volume confirmation (optional - MFI not extremely overbought)
            volume_buy = mfi < self.mfi_buy
            
            # All conditions must be met for strong buy signal
            if rsi_buy and macd_buy and volatility_buy and volume_buy:
                return 1
        
        # Sell Signal Conditions
        if downtrend:
            # Momentum conditions for sell: RSI not oversold
            rsi_sell = rsi > self.rsi_sell
            macd_sell = (macd < macd_signal) or (macd_diff < 0)  # MACD bearish
            volatility_sell = (bbp > self.bbp_sell_high) or (self.bbp_sell_low <= bbp <= self.bbp_sell_high)  # Near upper BB or middle range
            
            # Volume confirmation (optional - MFI not extremely oversold)
            volume_sell = mfi > self.mfi_sell
            
            # All conditions must be met for strong sell signal
            if rsi_sell and macd_sell and volatility_sell and volume_sell:
                return -1
        
        # Default: Hold
        return 0



bot = gptbasedstrategytabased()

# bot.local_development()
bot.run()

