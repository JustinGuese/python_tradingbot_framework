"""
Source: https://quants.md/?sort=sharpe
Strategy page: https://backtester.quantjourney.cloud/strategies/example-weights-11-quarterly-te-cost-gate
Upstream code: https://github.com/QuantJourneyOrg/quantjourney-bt/blob/main/strategies/example_weights_11_quarterly_te_cost_gate.py (Apache-2.0, (c) 2026 QuantJourney)

Quarterly momentum rotation across the ETF8 universe with a tracking-error and
turnover gate. The signal is the top 3 ETFs by trailing 126-bar return, with no
positivity filter, held at equal weight capped at 40%. The book rebalances on the
first bar of each calendar quarter. It also rebalances early when the held book's
annualised tracking error against SPY exceeds 6% over 63 bars, but only while
the rolling 365-day one-way turnover stays within 4.0x.

Departures from source:
  * The source gate (RebalancePolicy with tracking-error and turnover limits) is
    not in the source file. The gate here is a stateful approximation.
  * State lives on the instance, so each live CronJob run starts fresh. The first
    call always builds the book. Quarterly and tracking-error timing only apply
    within one process, which means inside backtests. This is a known limitation.

Universe: ETF8 (SPY, EFA, EEM, TLT, IEF, GLD, DBC, VNQ). SPY is the benchmark.
"""

from typing import Any, ClassVar

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF8, closes_frame, equal_weight, period_rolled
from tradingbot.utils.runner import run_bot

UNIVERSE = list(ETF8)
LOOKBACK = 126
TOP_N = 3
CAP = 0.40
TE_THRESHOLD = 0.06
TE_WINDOW = 63
MAX_ANNUAL_TURNOVER = 4.0
BENCHMARK = "SPY"
_TRADING_DAYS = 252
_TURNOVER_WINDOW_DAYS = 365


def compute_weights(
    closes: pd.DataFrame,
    lookback: int,
    top_n: int,
    cap: float,
    te_threshold: float,
    te_window: int,
    max_annual_turnover: float,
    state: dict[str, Any],
) -> dict[str, float] | None:
    """Return the target book when a rebalance is due, otherwise ``None``.

    ``closes`` holds history up to and including the current bar, one column
    per ticker. ``state`` is mutated in place and carries ``"last_target"``
    (the book held since the last rebalance, or ``None``) and ``"turnover"``
    (a list of ``(timestamp, one_way_turnover)`` pairs).

    Fewer than ``lookback + 1`` rows returns ``{}``. The first call ever
    always builds the book. Later calls rebalance on a quarter-first bar, or
    when the held book's annualised tracking error against SPY over the last
    ``te_window`` bars exceeds ``te_threshold``. A rebalance is skipped and
    returns ``None`` when it would push the rolling 365-day turnover above
    ``max_annual_turnover``.
    """
    if len(closes) < lookback + 1:
        return {}
    momentum = (closes.iloc[-1] / closes.iloc[-1 - lookback] - 1.0).dropna()
    picks = [str(t) for t in momentum.nlargest(top_n).index]
    proposed = equal_weight(picks, cap)

    last = state.get("last_target")
    if last is None:
        due = True
    else:
        due = period_rolled(closes.index, "Q")
        if not due and BENCHMARK in closes.columns and len(closes) > te_window:
            rets = closes.pct_change().iloc[-te_window:]
            held = sum(rets[t] * w for t, w in last.items() if t in rets.columns)
            te = float((held - rets[BENCHMARK]).std() * (_TRADING_DAYS**0.5))
            due = bool(te > te_threshold)
    if not due:
        return None

    prior = last or {}
    turnover = 0.5 * sum(abs(proposed.get(t, 0.0) - prior.get(t, 0.0)) for t in set(proposed) | set(prior))
    now = closes.index[-1]
    state["turnover"] = [(d, x) for d, x in state["turnover"] if (now - d).days <= _TURNOVER_WINDOW_DAYS]
    if last is not None and sum(x for _, x in state["turnover"]) + turnover > max_annual_turnover:
        return None
    state["turnover"].append((now, turnover))
    state["last_target"] = proposed
    return proposed


class QuantsmdQuarterlyTeCostGateBot(Bot):
    """
    Quarterly top-3 momentum on ETF8, rebalanced early on tracking-error breach.

    Args:
        lookback: Trailing bars for the momentum ranking.
        top_n: Number of ETFs held.
        cap: Maximum weight for any single ETF.
        te_threshold: Annualised tracking error vs SPY that forces an early rebalance.
        te_window: Bars used to measure tracking error.
        max_annual_turnover: Cap on one-way turnover summed over the last 365 days.

    The gate's state lives on the instance. Each live CronJob run starts with
    no state, so its first call always builds the book. Quarterly and
    tracking-error timing only apply within one process, such as a backtest.
    This is a known limitation.
    """

    # A 126-bar lookback with quarterly rebalances needs more than the default 1y.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "lookback": [63, 126, 189],
        "te_threshold": [0.04, 0.06, 0.08],
    }

    def __init__(
        self,
        lookback: int = LOOKBACK,
        top_n: int = TOP_N,
        cap: float = CAP,
        te_threshold: float = TE_THRESHOLD,
        te_window: int = TE_WINDOW,
        max_annual_turnover: float = MAX_ANNUAL_TURNOVER,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdQuarterlyTeCostGateBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Live fetch: must exceed the momentum lookback.
            period="2y",
            lookback=lookback,
            top_n=top_n,
            cap=cap,
            te_threshold=te_threshold,
            te_window=te_window,
            max_annual_turnover=max_annual_turnover,
            **kwargs,
        )
        self.lookback = lookback
        self.top_n = top_n
        self.cap = cap
        self.te_threshold = te_threshold
        self.te_window = te_window
        self.max_annual_turnover = max_annual_turnover
        self._state: dict[str, Any] = {"last_target": None, "turnover": []}

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float] | None:
        """Return the book on a rebalance bar, or ``None`` to leave it untouched."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        return compute_weights(
            closes,
            self.lookback,
            self.top_n,
            self.cap,
            self.te_threshold,
            self.te_window,
            self.max_annual_turnover,
            self._state,
        )


if __name__ == "__main__":
    run_bot(QuantsmdQuarterlyTeCostGateBot)
