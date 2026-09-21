"""
TARegimeMultiAssetBot — TARegimeAdaptiveBot's signal across uncorrelated assets.

TARegimeAdaptiveBot runs the Hurst-regime TA signal (utils.ta_regime) on SPY
alone. That is one bet on one asset, and over 2020-09..2026-09 it had no alpha
vs QQQ (+0.4%/yr, t 0.10); its +14% live alpha over ~5 months is best read as
luck. The same signal with the same parameters, applied to eight assets that do
not move together, had beta 0.11, max drawdown 9% vs 17%, and alpha +3.0%/yr
(t 1.56). The edge is small, but it is spread over independent bets, and that is
where a t-stat comes from. See docs/backtests/taregimemultiassetbot.md.

Sizing is the framework's multi-ticker default: one equal sleeve of 1/N per
asset, bought on a 1 and exited on a -1. A 0 holds what is already held and adds
nothing. Unfunded sleeves are cash, which the live copier parks in SHV.

A separate bot, not a change to TARegimeAdaptiveBot, so that bot's live track
record stays continuous and the two can be compared.
"""

from typing import Any, ClassVar

from tradingbot.utils.botclass import Bot
from tradingbot.utils.runner import run_bot
from tradingbot.utils.ta_regime import ta_regime_decision

# US equity, developed and emerging ex-US equity, long and intermediate
# Treasuries, gold, broad commodities, and the dollar. Every leg is a US ETF,
# so Collective2 can trade the whole book.
UNIVERSE = ["SPY", "EFA", "EEM", "TLT", "IEF", "GLD", "DBC", "UUP"]


class TARegimeMultiAssetBot(Bot):
    # These are TARegimeAdaptiveBot's live defaults, deliberately NOT re-tuned
    # on this universe: the case for this bot is that an unchanged signal holds
    # up across assets. Tuning per universe would erase exactly that evidence.
    param_grid: ClassVar[dict] = {
        "hurst_trend_threshold": [0.44, 0.46, 0.48],
        "adx_threshold": [14, 16, 18],
        "zscore_entry": [1.5, 1.75, 2.0],
    }

    # The 1d default of "1y" leaves ~200 evaluable bars after warmup. That is
    # too few to measure a small alpha, so backtests use five years.
    BACKTEST_PERIOD: ClassVar[str | None] = "5y"

    def __init__(
        self,
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
            "TARegimeMultiAssetBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Same margin as TARegimeAdaptiveBot: the signal is flat until it
            # has hurst_window + 2 bars, so the live fetch must comfortably
            # exceed that.
            period="1y",
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
        # Annotated for the same reason as in TARegimeAdaptiveBot: the mixed
        # int/float/bool literal would otherwise infer as dict[str, float].
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
        # Each asset's own history. Both call paths set self.datas[ticker] to
        # that ticker's frame (truncated to the current bar in a backtest) and
        # set self._current_ticker before asking.
        return ta_regime_decision(row, self.datas.get(self._current_ticker), **self._ta_params)


if __name__ == "__main__":
    run_bot(TARegimeMultiAssetBot)
