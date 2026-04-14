from utils.core import Bot


class XAUSyntheticMetalTreeBot(Bot):
    # Define the hyperparameter search space for this bot
    param_grid = {
        "dch_threshold": [315, 330, 350],
        "dcl_threshold": [310, 325, 345],
        "atr_threshold": [0.12, 0.14, 0.16],
        "ichimoku_base_threshold": [310, 325, 345],
        "kch_threshold": [315, 330, 350],
    }

    def __init__(
        self,
        dch_threshold: float = 350,
        dcl_threshold: float = 310,
        atr_threshold: float = 0.12,
        ichimoku_base_threshold: float = 310,
        kch_threshold: float = 315,
        **kwargs
    ):
        """
        Initialize the XAU Synthetic Metal Tree Bot with configurable thresholds.
        
        Args:
            dch_threshold: Threshold for volatility_dch indicator (default: 207.61)
            dcl_threshold: Threshold for volatility_dcl indicator (default: 204.44)
            atr_threshold: Threshold for volatility_atr indicator (default: 0.14)
            ichimoku_base_threshold: Threshold for trend_ichimoku_base indicator (default: 204.64)
            kch_threshold: Threshold for volatility_kch indicator (default: 207.33)
            **kwargs: Additional parameters passed to base class
        """
        super().__init__(
            "XAUSyntheticMetalTreeBot",
            "^XAU",
            dch_threshold=dch_threshold,
            dcl_threshold=dcl_threshold,
            atr_threshold=atr_threshold,
            ichimoku_base_threshold=ichimoku_base_threshold,
            kch_threshold=kch_threshold,
            **kwargs
        )
        # Store parameters as instance variables for easy access
        self.dch_threshold = dch_threshold
        self.dcl_threshold = dcl_threshold
        self.atr_threshold = atr_threshold
        self.ichimoku_base_threshold = ichimoku_base_threshold
        self.kch_threshold = kch_threshold

    def decisionFunction(self, row):
        if "volatility_dch" not in row.index:
            return 0
        if row["volatility_dch"] <= self.dch_threshold:
            if row["volatility_dcl"] <= self.dcl_threshold:
                return -1
            else:  # volatility_dcl > dcl_threshold
                if row["volatility_atr"] <= self.atr_threshold:
                    if row["trend_ichimoku_base"] <= self.ichimoku_base_threshold:
                        return -1
                    else:  # trend_ichimoku_base > ichimoku_base_threshold
                        return 1
                else:  # volatility_atr > atr_threshold
                    if row["volatility_kch"] <= self.kch_threshold:
                        return -1
                    else:  # volatility_kch > kch_threshold
                        return 1
        else:  # volatility_dch > dch_threshold
            return -1


bot = XAUSyntheticMetalTreeBot()

# bot.local_development()
bot.run()
#  12:26:01 - utils.botclass - INFO - Backtesting with best parameters...
# 2026-03-26 12:26:01 - utils.botclass - INFO - ============================================================
# 2026-03-26 12:26:01 - utils.botclass - INFO -   dch_threshold: 350
# 2026-03-26 12:26:01 - utils.botclass - INFO -   dcl_threshold: 310
# 2026-03-26 12:26:01 - utils.botclass - INFO -   atr_threshold: 0.12
# 2026-03-26 12:26:01 - utils.botclass - INFO -   ichimoku_base_threshold: 310
# 2026-03-26 12:26:01 - utils.botclass - INFO -   kch_threshold: 315
# [*********************100%***********************]  1 of 1 completed
# 2026-03-26 12:26:35 - utils.data_service - INFO - Adding only missing DataFrame rows to DB
# 2026-03-26 12:26:36 - utils.data_service - INFO - Rows to insert: 0
# 2026-03-26 12:26:50 - utils.backtest - INFO - QuantStats report → gs://tradingbotrunresults/XAUSyntheticMetalTreeBot/sharpewinner/report.html
# 2026-03-26 12:26:58 - utils.backtest - INFO - QuantStats report → gs://tradingbotrunresults/XAUSyntheticMetalTreeBot/yearlyreturnwinner/report.html
# 2026-03-26 12:26:58 - utils.botclass - INFO - 
# --- Backtest Results: XAUSyntheticMetalTreeBot ---
# 2026-03-26 12:26:58 - utils.botclass - INFO - Yearly Return: 7.21%
# 2026-03-26 12:26:58 - utils.botclass - INFO - Buy & Hold Return: -9.73%
# 2026-03-26 12:26:58 - utils.botclass - INFO - Outperformance vs B&H: +16.94%
# 2026-03-26 12:26:58 - utils.botclass - INFO - Sharpe Ratio: 7.58
# 2026-03-26 12:26:58 - utils.botclass - INFO - Number of Trades: 2
# 2026-03-26 12:26:58 - utils.botclass - INFO - Max Drawdown: 5.24%