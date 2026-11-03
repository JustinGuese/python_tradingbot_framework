"""
Variant of quantsmd_daily_partial_drift.

Daily momentum-proportional weights across the nine S&P sector ETFs, with
hysteresis on entry and a minimum holding period. Each sector's trailing return
over the lookback is measured. A sector enters the book only after its momentum
rises above +ENTER, and it leaves only after its momentum falls below -ENTER.
Once entered it is held for at least MIN_HOLD bars before it can flip again.
Held sectors are weighted by their latest momentum (floored at 0.001), as a
share of total held momentum, clipped at 35%. Any clipped excess stays in cash.

Departures from quantsmd_daily_partial_drift:
- A sector is held only while its momentum has been above +ENTER, and is dropped
  only after it falls below -ENTER. The source held any sector with positive
  momentum, so it churned on small moves. The source made 28k trades, and costs
  ate the edge.
- Minimum hold: a state flip is allowed only MIN_HOLD bars after the last flip.
- A held sector whose latest momentum is at or below zero keeps a 0.001 score,
  so it can stay in the book while inside the hysteresis band.
- The source's 10% partial-drift rebalance is still replaced by the framework's
  no-trade band. The book is returned on every bar and never ``None``.
- Weights are long-only and gross <= 1.0, as the framework requires.
"""

from typing import ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import SECTORS9, closes_frame
from tradingbot.utils.quantsmd_v2 import min_hold_state, schmitt_state
from tradingbot.utils.runner import run_bot

UNIVERSE = list(SECTORS9)
LOOKBACK = 63
CAP = 0.35
ENTER = 0.02
MIN_HOLD = 10
MIN_SCORE = 0.001


def compute_weights(
    closes: pd.DataFrame,
    lookback: int,
    cap: float,
    enter: float = ENTER,
    min_hold: int = MIN_HOLD,
) -> dict[str, float]:
    """Momentum-proportional weights over sectors held by the hysteresis rule.

    ``closes`` holds history up to and including the current bar, with one
    column per ticker. Fewer than ``lookback + 1`` rows returns ``{}``. The
    momentum series of a ticker is ``close / close.shift(lookback) - 1``. A
    Schmitt state turns on above ``+enter`` and off below ``-enter``, and
    ``min_hold_state`` applies ``min_hold`` bars between flips. A ticker is held
    when that state is True on the last bar and its latest momentum is defined.
    Each held ticker scores ``max(latest momentum, MIN_SCORE)``, and its weight is
    ``min(score / total, cap)``, so the clipped excess stays in cash. Returns
    ``{}`` when no ticker is held.
    """
    if len(closes) < lookback + 1:
        return {}
    mom_series = closes / closes.shift(lookback) - 1.0
    scores: dict[str, float] = {}
    for ticker in closes.columns:
        mom = mom_series[ticker]
        raw = schmitt_state(mom, enter_above=enter, exit_below=-enter)
        held = min_hold_state(raw, min_hold)
        latest = mom.iloc[-1]
        if bool(held.iloc[-1]) and pd.notna(latest):
            scores[str(ticker)] = max(float(latest), MIN_SCORE)
    if not scores:
        return {}
    total = sum(scores.values())
    out: dict[str, float] = {}
    for ticker, score in scores.items():
        weight = min(score / total, cap)
        if weight > 0:
            out[ticker] = weight
    return out


class QuantsmdDailyPartialDriftV2Bot(Bot):
    """
    Daily momentum-proportional sector book, with entry hysteresis and a minimum hold.

    Args:
        lookback: Trailing bars for the momentum score.
        cap: Maximum weight for any single sector. Clipped excess stays in cash.
        enter: Momentum threshold for entering (and, negated, for exiting) a sector.
        min_hold: Minimum bars between state flips for a sector.

    The book is returned on every bar and never ``None``.
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
        enter: float = ENTER,
        min_hold: int = MIN_HOLD,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdDailyPartialDriftV2Bot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must exceed the momentum lookback, or no sector is ever
            # evaluable and the bot holds cash without warning.
            period="1y",
            lookback=lookback,
            cap=cap,
            enter=enter,
            min_hold=min_hold,
            **kwargs,
        )
        self.lookback = lookback
        self.cap = cap
        self.enter = enter
        self.min_hold = min_hold

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the hysteresis momentum sector book for the current bar."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(
            closes,
            self.lookback,
            self.cap,
            enter=self.enter,
            min_hold=self.min_hold,
        )


if __name__ == "__main__":
    run_bot(QuantsmdDailyPartialDriftV2Bot)
