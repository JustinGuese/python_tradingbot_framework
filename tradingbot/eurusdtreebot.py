from utils.core import Bot


class EURUSDTreeBot(Bot):
    # Define a new hyperparameter grid using relative indicators
    # These are normalized (0-100 or centered around 0) and don't depend on price levels.
    param_grid = {
        "rsi_oversold": [30, 35, 40],
        "rsi_overbought": [60, 65, 70],
        "macd_diff_threshold": [-0.0005, 0.0, 0.0005],
        "bb_p_threshold": [0.05, 0.1, 0.15],
    }
    
    def __init__(
        self,
        rsi_oversold=35,
        rsi_overbought=65,
        macd_diff_threshold=0.0,
        bb_p_threshold=0.1,
        **kwargs
    ):
        """
        EURUSD Tree Bot using relative indicators.
        
        Args:
            rsi_oversold: RSI level for bullish bias (default: 35)
            rsi_overbought: RSI level for bearish bias (default: 65)
            macd_diff_threshold: Minimum MACD Histogram value (default: 0.0)
            bb_p_threshold: Threshold for Bollinger %B mean reversion (default: 0.1)
            **kwargs: Additional parameters passed to base class
        """
        # Increased period to 2y for proper TA warmup and robust backtesting
        super().__init__(
            "EURUSDTreeBot",
            "EURUSD=X",
            interval="1d",
            period="2y",
            rsi_oversold=rsi_oversold,
            rsi_overbought=rsi_overbought,
            macd_diff_threshold=macd_diff_threshold,
            bb_p_threshold=bb_p_threshold,
            **kwargs
        )
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.macd_diff_threshold = macd_diff_threshold
        self.bb_p_threshold = bb_p_threshold

    def decisionFunction(self, row):
        """
        Decision function for EURUSD trading using relative/normalized indicators.
        
        Args:
            row: Pandas Series with market data and technical indicators
            
        Returns:
            -1: Sell signal
             0: Hold (no action)
             1: Buy signal
        """
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
        
        close = safe_get("close")
        if close <= 0:
            return 0
            
        # 1. Trend Filter: Price vs SMA (Relative)
        sma = safe_get("trend_sma_slow")
        
        # 2. Momentum: RSI (0-100)
        rsi = safe_get("momentum_rsi", 50.0)
        
        # 3. Trend Confirmation: MACD Difference (Histogram)
        # trend_macd_diff is (macd - macd_signal)
        macd_diff = safe_get("trend_macd_diff")
        
        # 4. Volatility: Bollinger Band %B (Normalized position)
        bbh = safe_get("volatility_bbh")
        bbl = safe_get("volatility_bbl")
        bb_width = bbh - bbl
        b_percent = (close - bbl) / bb_width if bb_width > 0 else 0.5

        # --- Tree-based decision logic ---
        # Branch 1: Bullish Trend (Price above SMA)
        if close > sma:
            if rsi < self.rsi_overbought:
                if macd_diff > self.macd_diff_threshold:
                    return 1 # Strong Buy: Trend + Momentum + MACD confirm
                else:
                    return 0 # Neutral: Trend is up but MACD is weakening
            else:
                return -1 # Sell/Take Profit: Overbought in an uptrend
        
        # Branch 2: Bearish Trend (Price below SMA)
        else:
            if rsi > self.rsi_oversold:
                if macd_diff < -self.macd_diff_threshold:
                    return -1 # Strong Sell: Trend + Momentum + MACD confirm
                else:
                    return 0 # Neutral: Trend is down but MACD is bottoming
            else:
                # Mean Reversion Opportunity: Extreme oversold below BB in a downtrend
                if b_percent < self.bb_p_threshold:
                    return 1 
                return 0


bot = EURUSDTreeBot()

bot.run()
# For local development, run optimization + backtest
# bot.local_development()

# Live execution:
# bot.local_backtest()
