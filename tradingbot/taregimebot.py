"""
TA-only regime-adaptive bot: historic data only (no Fear & Greed).

Regime and signal logic live in utils.ta_regime; this bot only fetches data
and calls ta_regime_decision.
"""

from typing import Any, ClassVar

from tradingbot.utils.botclass import Bot
from tradingbot.utils.runner import run_bot
from tradingbot.utils.ta_regime import ta_regime_decision


class TARegimeAdaptiveBot(Bot):
    """
    Single-asset bot that uses a Hurst-style regime (trend vs mean-reversion)
    and TA indicators from historic OHLCV only. All decision logic is in
    utils.ta_regime; the bot delegates decisionFunction to ta_regime_decision.
    """

    # Grid centered around best params (from prior tuning: ~12.58% return, 2.65 Sharpe)
    param_grid: ClassVar[dict] = {
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
        # Annotated because the literal below mixes int, float and bool, so it
        # infers as dict[str, float] and the **unpack into ta_regime_decision
        # then mismatches its int/bool parameters. The values are correct; only
        # the inferred element type is lossy.
        self._ta_params: dict[str, Any] = {
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


# Backtest transcript (best params, max-sharpe): see docs/backtests/taregimebot.md
if __name__ == "__main__":
    # bot.local_development(objective="yearly_return", param_sample_ratio=.1)
    run_bot(TARegimeAdaptiveBot)
