"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-weights-10-monthly-circuit-breaker
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_10_monthly_circuit_breaker.py (Apache-2.0, (c) 2026 QuantJourney)

Monthly SMA(50/200) trend book on eight ETFs (SPY, EFA, EEM, TLT, IEF, GLD, DBC,
VNQ), the same book as QuantsmdMonthlyDriftEtfBot, wrapped in a circuit breaker.
The book is recomputed on the first bar of each calendar month. The breaker
models the sleeve's own value: when it falls more than 15% below its peak, the
book goes to cash and stays there for 10 bars. After the cooldown the book is
only rebuilt on the next month start, and the peak is reset to the value at
that point.

Departures from source:
- nav is a model of this bot's own sleeve (weights held fixed between
  rebalances), not the account value.
- The source's 5% drift band is replaced by the framework's own no-trade band.
- The bot holds no state between CronJob runs, so the breaker is replayed over
  the last 504 bars on every call. A drawdown that started before that window
  is not carried in.
"""

from typing import ClassVar

import pandas as pd

from tradingbot.quantsmd_monthly_drift_etf import compute_weights as drift_book
from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF8, closes_frame
from tradingbot.utils.runner import run_bot

UNIVERSE = list(ETF8)
FAST = 50
SLOW = 200
CAP = 0.30
BREAKER = 0.15
COOLDOWN = 10
REPLAY = 504
MIN_ROWS = 200


def compute_weights(
    closes: pd.DataFrame,
    fast: int = FAST,
    slow: int = SLOW,
    cap: float = CAP,
    breaker: float = BREAKER,
    cooldown: int = COOLDOWN,
    replay: int = REPLAY,
) -> dict[str, float]:
    """Return the monthly book after replaying the circuit breaker over the tail.

    ``closes`` holds history up to and including the current bar, with one
    column per ticker. Fewer than ``MIN_ROWS`` rows returns ``{}``. The breaker
    is replayed over the last ``replay`` rows, with the base book rebuilt from
    all history up to each bar so the SMAs are fully warmed. For each row after
    the first: the sleeve's return uses the book held before that row; the peak
    is updated; a 15%-style drawdown below the peak with a non-empty book clears
    the book and starts the cooldown; otherwise, when the cooldown has run out
    and the row starts a month, the base book is taken and a tripped breaker
    resets its peak. The result is the book on the last bar and is never None.
    A drawdown is "more than ``breaker``" below the peak, measured as nav / peak - 1.
    """
    if len(closes) < MIN_ROWS:
        return {}
    window = closes.tail(replay)
    offset = len(closes) - len(window)
    rets = window.pct_change().fillna(0.0)
    nav = 1.0
    peak = 1.0
    book: dict[str, float] = {}
    cool = 0
    tripped = False
    for i in range(1, len(window)):
        nav *= 1.0 + sum(w * float(rets.iloc[i][t]) for t, w in book.items())
        peak = max(peak, nav)
        if cool > 0:
            cool -= 1
        if nav / peak - 1.0 < -breaker and book:
            book = {}
            cool = cooldown
            tripped = True
        elif cool == 0:
            base = drift_book(closes.iloc[: offset + i + 1], fast, slow, cap)
            if base is not None:
                book = base
                if tripped:
                    peak = nav
                    tripped = False
    return dict(book)


class QuantsmdMonthlyCircuitBreakerBot(Bot):
    """
    Monthly SMA(50/200) trend book on eight ETFs, 30% cap, with a drawdown breaker.

    Args:
        fast: Fast simple moving average length in daily bars.
        slow: Slow simple moving average length in daily bars.
        cap: Maximum weight for any single ETF.
        breaker: Drawdown from the modelled sleeve's peak that clears the book.
        cooldown: Bars the book stays in cash after the breaker trips.

    Returns the monthly book on every bar (the book only changes on month
    starts or when the breaker trips), so it never returns ``None``.
    """

    # A 200-bar SMA and the 504-bar breaker replay need more than the default 1y.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "breaker": [0.10, 0.15, 0.20],
        "cooldown": [5, 10, 20],
    }

    def __init__(
        self,
        fast: int = FAST,
        slow: int = SLOW,
        cap: float = CAP,
        breaker: float = BREAKER,
        cooldown: int = COOLDOWN,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdMonthlyCircuitBreakerBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must cover the 504-bar breaker replay plus the 200-bar
            # SMA warmup, or the replayed breaker state is wrong.
            period="3y",
            fast=fast,
            slow=slow,
            cap=cap,
            breaker=breaker,
            cooldown=cooldown,
            **kwargs,
        )
        self.fast = fast
        self.slow = slow
        self.cap = cap
        self.breaker = breaker
        self.cooldown = cooldown

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the breaker-adjusted ETF book for the current bar."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(
            closes,
            self.fast,
            self.slow,
            self.cap,
            self.breaker,
            self.cooldown,
        )


if __name__ == "__main__":
    run_bot(QuantsmdMonthlyCircuitBreakerBot)
