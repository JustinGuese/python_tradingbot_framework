"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-orders-02-market-rsi-reversion
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_orders_02_market_rsi_reversion.py (Apache-2.0, (c) 2026 QuantJourney)

Market-order RSI reversion on SPY, QQQ and IWM. Each ETF's 14-bar RSI is tracked
with hysteresis: it is bought when RSI falls below 35 and sold when RSI rises
above 60. A held ETF is sized at 12% of NAV, and the rest of NAV stays in cash.

The source fills market orders on the decision bar. The bot holds no state
between CronJob runs, so the RSI hold flag is replayed over the last 250 bars on
every call. A position opened earlier than that window is not carried in.

Departures from source:
  * No resting orders. Only market orders are used, so the replay keeps no
    pending-order list. Fills happen at the bar's close, not at an intrabar price.
  * Position size is a fixed 12% of NAV while held. The source does not
    rebalance; the framework's no-trade band absorbs the drift.
"""

from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF3, closes_frame, rsi, rsi_hysteresis
from tradingbot.utils.runner import run_bot

UNIVERSE = list(ETF3)
RSI_PERIOD = 14
ENTRY = 35.0
EXIT = 60.0
FRACTION = 0.12
REPLAY_BARS = 250


def _held_at_end(close: pd.Series, rsi_period: int, entry: float, exit_: float, replay: int) -> bool:
    """Return the RSI hold flag on the last bar, replayed over the last ``replay`` bars.

    RSI is computed on the whole series, so its warmup is not cut by the replay
    window. Only the tail of RSI values is walked through the hysteresis.
    """
    series = close.dropna().astype(float)
    if series.empty:
        return False
    rsi_values = rsi(series, rsi_period)
    return rsi_hysteresis(rsi_values.iloc[-replay:], entry, exit_)


def compute_weights(
    closes: pd.DataFrame,
    rsi_period: int = RSI_PERIOD,
    entry: float = ENTRY,
    exit_: float = EXIT,
    fraction: float = FRACTION,
    replay: int = REPLAY_BARS,
) -> dict[str, float]:
    """Hold ``fraction`` of NAV in every ticker whose RSI hold flag is set on the last bar.

    ``closes`` holds history up to and including the current bar, with one column
    per ticker. Returns ``{}`` when nothing is held, including when the history is
    shorter than the RSI warmup. Weights are never ``None``: this bot trades on
    every bar.
    """
    weights: dict[str, float] = {}
    for ticker in closes.columns:
        if _held_at_end(closes[ticker], rsi_period, entry, exit_, replay):
            weights[str(ticker)] = fraction
    return weights


class QuantsmdMarketRsiReversionBot(Bot):
    """
    Market-order RSI reversion on SPY, QQQ and IWM, 12% of NAV per held ETF.

    Args:
        rsi_period: RSI lookback in daily bars.
        entry: Buy when RSI falls below this level.
        exit: Sell when RSI rises above this level.
        fraction: Fixed share of NAV for each held ETF.

    Trades on every bar. Two order-style departures apply: there are no resting
    orders, and the position size is fixed rather than rebalanced.
    """

    # The RSI warmup plus the replay needs more than the default 1y backtest window.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "entry": [30, 35, 40],
        "exit": [55, 60, 65],
    }

    def __init__(
        self,
        rsi_period: int = RSI_PERIOD,
        entry: float = ENTRY,
        exit: float = EXIT,
        fraction: float = FRACTION,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdMarketRsiReversionBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must exceed the RSI warmup plus the replay length, or the
            # hold flag cannot be rebuilt and the bot quietly sits in cash.
            period="2y",
            rsi_period=rsi_period,
            entry=entry,
            exit=exit,
            fraction=fraction,
            **kwargs,
        )
        self.rsi_period = rsi_period
        self.entry = entry
        self.exit = exit
        self.fraction = fraction

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the ETF book for the current bar."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes, self.rsi_period, self.entry, self.exit, self.fraction)


if __name__ == "__main__":
    run_bot(QuantsmdMarketRsiReversionBot)
