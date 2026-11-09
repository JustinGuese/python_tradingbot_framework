"""
Country-ETF trend book with its own-index regime gate (paper).

Holds a country ETF while its SMA50 is above its SMA200, equal weight capped at 25%, whatever the cap leaves
stays in cash. The whole book is then multiplied by a regime factor: 1.0 while the equal-weight index of the
20 country ETFs closes above its own 200-bar SMA, ``off_scale`` (0 = cash) otherwise.

This is QuantsmdSmaDailyGatedBot's rule on a different universe, untuned (50 / 200 / 0.25 / 200 / cash).
docs/backtests/gate-other-markets-2026-11.md, 1996-2026, alpha vs QQQ:

  * +5.3%/yr, t 2.32, beta 0.20, max DD -25% (the same book ungated: +1.9%, t 0.75, DD -48%);
  * the gate adds +3.3%/yr over the ungated book (t 1.89), positive in both halves;
  * but H2 alpha is -0.6%: nearly all of the alpha is 1996-2006 (+12.6%, t 2.9), 2013-2019 is -4.2%.

The framework's own backtest_bot over 2003-2026 (the window all 20 ETFs share) is weaker: +4.0%/yr, t 1.67, beta 0.29,
max DD -24%, H1 +7.4% (t 2.01), H2 +0.7% (t 0.24).

So it does NOT clear the project bar (t >= 2 AND positive in both halves) and is UNPROVEN. It exists to collect
a live record, which ``alphareport`` will call `too short` for months. Paper only: not wired into any copier.

The gate index is built from the 20 ETFs themselves (no extra ticker), exactly as in the study.
"""

from __future__ import annotations

from typing import ClassVar

import pandas as pd

from tradingbot import quantsmd_sma_daily as base
from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import closes_frame
from tradingbot.utils.quantsmd_v2 import scale_book
from tradingbot.utils.runner import run_bot

UNIVERSE = [
    "EWJ", "EWG", "EWU", "EWC", "EWA", "EWH", "EWS", "EWY", "EWT", "EWZ",
    "EWW", "EWP", "EWQ", "EWI", "EWL", "EWD", "EWN", "EWK", "EWM", "EZA",
]  # fmt: skip
FAST = base.FAST
SLOW = base.SLOW
CAP = base.CAP
GATE_WINDOW = 200
OFF_SCALE = 0.0


def ew_index(closes: pd.DataFrame) -> pd.Series:
    """Equal-weight index (level) of the members that have a return each day. A missing return counts as 0."""
    returns = closes.pct_change(fill_method=None)
    return (1 + returns.mean(axis=1).fillna(0.0)).cumprod() * 100


def gate_factor(closes: pd.DataFrame, window: int = GATE_WINDOW, off_scale: float = OFF_SCALE) -> float:
    """1.0 while the equal-weight index closes above its ``window`` SMA, else ``off_scale``.

    Fails open (1.0) when there are fewer than ``window`` rows, so a short history never forces cash.
    """
    if len(closes) < window:
        return 1.0
    index = ew_index(closes)
    sma = index.rolling(window, min_periods=window).mean().iloc[-1]
    if pd.isna(sma):
        return 1.0
    return 1.0 if index.iloc[-1] > sma else off_scale


def compute_weights(
    closes: pd.DataFrame,
    fast: int = FAST,
    slow: int = SLOW,
    cap: float = CAP,
    off_scale: float = OFF_SCALE,
    gate_window: int = GATE_WINDOW,
) -> dict[str, float]:
    """The SMA50/200 country book, scaled by the own-index gate. ``{}`` is all cash."""
    book = base.compute_weights(closes, fast, slow, cap)
    return scale_book(book, gate_factor(closes, gate_window, off_scale))


class CountryTrendGateBot(Bot):
    """
    Daily country-ETF SMA trend book gated by the universe's own 200-bar trend (paper, unproven).

    Args:
        fast: Fast SMA length in daily bars.
        slow: Slow SMA length in daily bars.
        cap: Maximum weight per country.
        off_scale: Multiplier on the whole book while the gate is off (0.0 = cash).
        gate_window: SMA length of the gate index.

    No ``param_grid`` on purpose: the rule is untuned, and tuning it on the one history it was tested on would
    only manufacture a better-looking backtest.
    """

    BACKTEST_PERIOD: ClassVar[str | None] = "max"
    # One country ETF with a missing bar should not skip the run; it sits out and any holding is pinned.
    MIN_UNIVERSE_COVERAGE: ClassVar[float] = 0.9

    def __init__(
        self,
        fast: int = FAST,
        slow: int = SLOW,
        cap: float = CAP,
        off_scale: float = OFF_SCALE,
        gate_window: int = GATE_WINDOW,
        **kwargs,
    ):
        super().__init__(
            "CountryTrendGateBot",
            tickers=list(UNIVERSE),
            interval="1d",
            # Must exceed both the slow SMA and the gate window, or the book and the gate stay flat.
            period="2y",
            fast=fast,
            slow=slow,
            cap=cap,
            off_scale=off_scale,
            gate_window=gate_window,
            **kwargs,
        )
        self.fast = fast
        self.slow = slow
        self.cap = cap
        self.off_scale = off_scale
        self.gate_window = gate_window

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the gated country book for the current bar."""
        closes = closes_frame(self.datas, self.tickers)
        return compute_weights(closes, self.fast, self.slow, self.cap, self.off_scale, self.gate_window)


if __name__ == "__main__":
    run_bot(CountryTrendGateBot)
