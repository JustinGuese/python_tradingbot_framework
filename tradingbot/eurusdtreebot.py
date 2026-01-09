from utils.botclass import Bot


class EURUSDTreeBot(Bot):
    # Define the hyperparameter search space for this bot
    param_grid = {
        "sma_slow_threshold": [1.14, 1.16, 1.18],
        "macd_signal_threshold": [-0.01, -0.00, 0.01],
        "bbh_threshold": [1.13, 1.15, 1.17],
        "ichimoku_b_threshold": [1.13, 1.15, 1.17],
        "dcl_threshold": [1.13, 1.15, 1.17],
    }
    
    def __init__(
        self,
         sma_slow_threshold: 1.16,
        macd_signal_threshold: -0.01,
        bbh_threshold: 1.15,
        ichimoku_b_threshold: 1.15,
        dcl_threshold: 1.15,
        **kwargs
    ):
        """
        Initialize the EURUSD Tree Bot with configurable thresholds.
        
        Args:
            sma_slow_threshold: Threshold for trend_sma_slow indicator (default: 1.16)
            macd_signal_threshold: Threshold for trend_macd_signal indicator (default: -0.00)
            bbh_threshold: Threshold for volatility_bbh indicator (default: 1.15)
            ichimoku_b_threshold: Threshold for trend_visual_ichimoku_b indicator (default: 1.15)
            dcl_threshold: Threshold for volatility_dcl indicator (default: 1.15)
            **kwargs: Additional parameters passed to base class
        """
        super().__init__(
            "EURUSDTreeBot",
            "EURUSD=X",
            interval="1d",
            period="1mo",
            sma_slow_threshold=sma_slow_threshold,
            macd_signal_threshold=macd_signal_threshold,
            bbh_threshold=bbh_threshold,
            ichimoku_b_threshold=ichimoku_b_threshold,
            dcl_threshold=dcl_threshold,
            **kwargs
        )
        # Store parameters as instance variables for easy access
        self.sma_slow_threshold = sma_slow_threshold
        self.macd_signal_threshold = macd_signal_threshold
        self.bbh_threshold = bbh_threshold
        self.ichimoku_b_threshold = ichimoku_b_threshold
        self.dcl_threshold = dcl_threshold

    def decisionFunction(self, row):
        """
        Decision function for EURUSD trading using tree-based logic.
        
        Args:
            row: Pandas Series with market data and technical indicators
            
        Returns:
            -1: Sell signal
             0: Hold (no action) - returned if data is invalid
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
        
        # Get indicator values with NaN handling
        sma_slow = safe_get("trend_sma_slow", 0.0)
        macd_signal = safe_get("trend_macd_signal", 0.0)
        bbh = safe_get("volatility_bbh", 0.0)
        ichimoku_b = safe_get("trend_visual_ichimoku_b", 0.0)
        dcl = safe_get("volatility_dcl", 0.0)
        
        # Check if we have valid price data
        close_price = safe_get("close", 0.0)
        if close_price <= 0:
            return 0  # Invalid price data, hold
        
        # Tree-based decision logic
        if sma_slow <= self.sma_slow_threshold:
            if macd_signal <= self.macd_signal_threshold:
                return -1
            else:  # trend_macd_signal > macd_signal_threshold
                if bbh <= self.bbh_threshold:
                    if ichimoku_b <= self.ichimoku_b_threshold:
                        # Both branches return 1 regardless of trend_kst_diff
                        return 1
                    else:  # trend_visual_ichimoku_b > ichimoku_b_threshold
                        if dcl <= self.dcl_threshold:
                            return -1
                        else:
                            return 1
                else:  # volatility_bbh > bbh_threshold
                    return 1
        else:  # trend_sma_slow > sma_slow_threshold
            return -1


bot = EURUSDTreeBot()

# bot.local_development()

# ============================================================
# Best parameters (paste into __init__ defaults):
# ============================================================
#     sma_slow_threshold: 1.16,
#     macd_signal_threshold: -0.01,
#     bbh_threshold: 1.15,
#     ichimoku_b_threshold: 1.15,
#     dcl_threshold: 1.15,


# ============================================================
# Backtesting with best parameters...
# ============================================================

# --- Backtest Results: EURUSDTreeBot ---
# Yearly Return: 3.85%
# Sharpe Ratio: 0.07
# Number of Trades: 76
# Max Drawdown: 1.01%
bot.run()
