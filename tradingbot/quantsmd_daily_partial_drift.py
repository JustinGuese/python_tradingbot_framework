"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-weights-12-daily-partial-drift
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_12_daily_partial_drift.py (Apache-2.0, (c) 2026 QuantJourney)

Daily momentum-proportional weights across the nine S&P sector ETFs. Each
sector's trailing return over the lookback is measured, only positive
momentum is held, and each held sector's weight is its share of total
positive momentum, clipped at 35%. Any clipped excess stays in cash.

Departures from source:
- The source's 10% partial-drift rebalance cannot be reproduced statelessly.
  It is replaced by the framework's own no-trade band, so the book is
  returned on every bar and the band absorbs small drift.
- Weights are long-only and gross <= 1.0, as the framework requires. The
  source is already long-only, so nothing else changes.
"""

from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import SECTORS9, closes_frame
from tradingbot.utils.runner import run_bot

UNIVERSE = list(SECTORS9)
LOOKBACK = 63
CAP = 0.35


def compute_weights(
    closes: pd.DataFrame,
    lookback: int,
    cap: float,
) -> dict[str, float]:
    """Momentum-proportional weights on positive trailing returns, clipped at ``cap``.

    ``closes`` holds history up to and including the current bar, with one
    column per ticker. Fewer than ``lookback + 1`` rows returns ``{}``. The
    momentum of a ticker is ``close[-1] / close[-1 - lookback] - 1``; only
    scores above zero are kept. Each weight is ``min(momentum / sum, cap)``,
    so the clipped excess stays in cash rather than being redistributed.
    Returns ``{}`` when no ticker has positive momentum.
    """
    if len(closes) < lookback + 1:
        return {}
    momentum = (closes.iloc[-1] / closes.iloc[-1 - lookback] - 1.0).dropna()
    momentum = momentum[momentum > 0]
    if momentum.empty:
        return {}
    total = float(momentum.sum())
    out: dict[str, float] = {}
    for ticker, score in momentum.items():
        weight = min(float(score) / total, cap)
        if weight > 0:
            out[str(ticker)] = weight
    return out


class QuantsmdDailyPartialDriftBot(Bot):
    """
    Daily momentum-proportional sector book, positive momentum only, 35% cap.

    Args:
        lookback: Trailing bars for the momentum score.
        cap: Maximum weight for any single sector. Clipped excess stays in cash.

    The source's partial-drift rebalance is replaced by the framework's no-trade
    band. The book is returned on every bar and never ``None``.
    """

    # A 63-bar momentum window fits inside the default 1y backtest, but the
    # max period keeps the backtest on the same long history as the other daily bots.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "lookback": [21, 63, 126],
    }

    def __init__(
        self,
        lookback: int = LOOKBACK,
        cap: float = CAP,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdDailyPartialDriftBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must exceed the momentum lookback, or no sector is ever
            # evaluable and the bot holds cash without warning.
            period="1y",
            lookback=lookback,
            cap=cap,
            **kwargs,
        )
        self.lookback = lookback
        self.cap = cap

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the momentum-proportional sector book for the current bar."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(closes, self.lookback, self.cap)


if __name__ == "__main__":
    run_bot(QuantsmdDailyPartialDriftBot)
