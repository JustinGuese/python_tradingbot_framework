"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-weights-06-signal-change-defensive
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_06_signal_change_defensive.py (Apache-2.0, (c) 2026 QuantJourney)

Regime switch on SPY against its 200-day simple moving average. Above the
average the book is equal-weight risk-on assets (SPY, EFA, EEM, DBC, VNQ).
Below it the book is equal-weight defensive assets (TLT, IEF, GLD). Each name
is capped at 50%, and the cap's excess stays in cash.

Departures from source:
- The source trades only when the regime changes. Here the book is returned on
  every bar and the framework's no-trade band absorbs the drift.
- Long-only and gross <= 1.0 are already true of the source, so nothing else
  changes.
"""

from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF8, closes_frame, equal_weight
from tradingbot.utils.runner import run_bot

UNIVERSE = list(ETF8)
RISK_ON = ["SPY", "EFA", "EEM", "DBC", "VNQ"]
DEFENSIVE = ["TLT", "IEF", "GLD"]
REGIME_TICKER = "SPY"
WINDOW = 200
CAP = 0.50


def compute_weights(
    closes: pd.DataFrame,
    window: int,
    cap: float,
) -> dict[str, float]:
    """Pick the risk-on or defensive book from SPY's position against its SMA.

    ``closes`` holds history up to and including the current bar, with one
    column per ticker. Fewer than ``window`` rows returns ``{}``, and so does a
    missing SPY column or an undefined average on the last bar. When SPY's last
    close is strictly above its average the risk-on names are active, otherwise
    the defensive names are. Only names present in ``closes`` are kept, and each
    active name gets ``min(1/len(active), cap)``.
    """
    if len(closes) < window or REGIME_TICKER not in closes.columns:
        return {}
    spy = closes[REGIME_TICKER]
    sma = spy.rolling(window, min_periods=window).mean().iloc[-1]
    if pd.isna(sma):
        return {}
    group = RISK_ON if spy.iloc[-1] > sma else DEFENSIVE
    active = [t for t in group if t in closes.columns]
    return equal_weight(active, cap)


class QuantsmdSignalChangeDefensiveBot(Bot):
    """
    SPY 200-day regime switch between a risk-on and a defensive ETF book.

    Args:
        window: Simple moving average length in daily bars for the SPY regime.
        cap: Maximum weight for any single name.

    The source's regime-change trigger is replaced by a book returned on every
    bar. It never returns ``None``.
    """

    # A 200-bar SMA needs more than the default 1y backtest window.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "window": [150, 200, 250],
    }

    def __init__(
        self,
        window: int = WINDOW,
        cap: float = CAP,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdSignalChangeDefensiveBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must exceed the SMA window, or the regime is never
            # evaluable and the bot holds cash without warning.
            period="2y",
            window=window,
            cap=cap,
            **kwargs,
        )
        self.window = window
        self.cap = cap

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the regime book for the current bar."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes, self.window, self.cap)


if __name__ == "__main__":
    run_bot(QuantsmdSignalChangeDefensiveBot)
