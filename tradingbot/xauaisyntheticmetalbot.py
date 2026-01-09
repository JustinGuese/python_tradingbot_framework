from utils.botclass import Bot


class XAUSyntheticMetalTreeBot(Bot):
    # Define the hyperparameter search space for this bot
    param_grid = {
        "dch_threshold": [205, 207.61, 210],
        "dcl_threshold": [202, 204.44, 207],
        "atr_threshold": [0.12, 0.14, 0.16],
        "ichimoku_base_threshold": [202, 204.64, 207],
        "kch_threshold": [205, 207.33, 210],
    }
    
    def __init__(
        self,
        dch_threshold: float = 207.61,
        dcl_threshold: float = 204.44,
        atr_threshold: float = 0.14,
        ichimoku_base_threshold: float = 204.64,
        kch_threshold: float = 207.33,
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
