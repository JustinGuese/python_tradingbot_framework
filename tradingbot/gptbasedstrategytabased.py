from typing import ClassVar

from tradingbot.utils.botclass import Bot
from tradingbot.utils.indicators import safe_get
from tradingbot.utils.runner import run_bot


class gptbasedstrategytabased(Bot):
    # Refined hyperparameter search space including requested exit logic
    param_grid: ClassVar[dict] = {
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
        **kwargs,
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
            **kwargs,
        )

    def decisionFunction(self, row) -> int:
        """
        Improved Decision function using trend-following with a volatility-aware exit.
        """
        close = safe_get(row, "close", 0.0)
        if close <= 0:
            return 0

        # 1. Trend Indicators
        sma_50 = safe_get(row, "trend_sma_fast")  # 50-day proxy
        sma_200 = safe_get(row, "trend_sma_slow")  # 200-day proxy
        adx = safe_get(row, "trend_adx")

        # 2. Momentum & Volatility
        rsi = safe_get(row, "momentum_rsi", 50.0)
        macd_diff = safe_get(row, "trend_macd_diff", 0.0)
        bbp = safe_get(row, "volatility_bbp", 0.5)

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

        if (
            close > sma_50
            and sma_50 > sma_200
            and adx > self.adx_threshold
            and rsi < self.rsi_buy
            and macd_diff > 0
            and bbp < self.bbp_buy_threshold
        ):
            return 1

        # --- EXIT LOGIC (Risk Management) ---
        # Exit when:
        # - Price falls below the 50 SMA by the 'sell_buffer' percentage
        # - RSI hits the 'extreme' threshold (vix_rsi_exit)

        exit_threshold = sma_50 * (1 - self.sell_buffer)

        if close < exit_threshold or rsi > self.vix_rsi_exit:
            return -1

        return 0


if __name__ == "__main__":
    run_bot(gptbasedstrategytabased)
    # Start with a backtest of the new logic: bot.local_backtest()
# Backtest transcript: see docs/backtests/gptbasedstrategytabased.md
