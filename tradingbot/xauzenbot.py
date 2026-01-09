from utils.botclass import Bot


class XAUZenbotTreeBot(Bot):
    # Define the hyperparameter search space for this bot
    param_grid = {
        "ichimoku_b_threshold": [200, 204.56, 210],
        "atr_threshold": [0.06, 0.08, 0.10],
        "roc_threshold": [0.25, 0.29, 0.33],
        "adx_threshold": [10, 11.80, 15],
        "dcl_threshold": [200, 203.66, 210],
    }
    
    def __init__(
        self,
        ichimoku_b_threshold: float = 204.56,
        atr_threshold: float = 0.08,
        roc_threshold: float = 0.29,
        adx_threshold: float = 11.80,
        dcl_threshold: float = 203.66,
        **kwargs
    ):
        """
        Initialize the XAU Zenbot Tree Bot with configurable thresholds.
        
        Args:
            ichimoku_b_threshold: Threshold for trend_visual_ichimoku_b indicator (default: 204.56)
            atr_threshold: Threshold for volatility_atr indicator (default: 0.08)
            roc_threshold: Threshold for momentum_roc indicator (default: 0.29)
            adx_threshold: Threshold for trend_adx indicator (default: 11.80)
            dcl_threshold: Threshold for volatility_dcl indicator (default: 203.66)
            **kwargs: Additional parameters passed to base class
        """
        super().__init__(
            "XAUZenbotTreeBot",
            "^XAU",
            ichimoku_b_threshold=ichimoku_b_threshold,
            atr_threshold=atr_threshold,
            roc_threshold=roc_threshold,
            adx_threshold=adx_threshold,
            dcl_threshold=dcl_threshold,
            **kwargs
        )
        # Store parameters as instance variables for easy access
        self.ichimoku_b_threshold = ichimoku_b_threshold
        self.atr_threshold = atr_threshold
        self.roc_threshold = roc_threshold
        self.adx_threshold = adx_threshold
        self.dcl_threshold = dcl_threshold

    def decisionFunction(self, row):
        if row["trend_visual_ichimoku_b"] <= self.ichimoku_b_threshold:
            if row["volatility_atr"] <= self.atr_threshold:
                if row["momentum_roc"] <= self.roc_threshold:
                    if row["trend_adx"] <= self.adx_threshold:
                        return -1
                    else:
                        return 1
                else:
                    return -1
            else:
                if row["volatility_dcl"] <= self.dcl_threshold:
                    return 1
                else:
                    return -1
        else:
            return -1


bot = XAUZenbotTreeBot()

# bot.local_development()
bot.run()
