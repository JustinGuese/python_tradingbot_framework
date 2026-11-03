"""
Ensemble of the twelve order-style quants.md bots, vol-targeted, with idle cash in SHV.

Each order-style component (bracket, stop, stop-limit, trailing-stop, OCO and limit
bots on SPY, QQQ and IWM) is invested only 10-20% of the time, so alone it is a
cash book with beta near 0.1. This bot runs all twelve replays, averages their
books per ticker with equal component weights, boosts the sum by ``sleeve_boost``
so a name held by every component reaches roughly 0.6-0.7 weight, and then
volatility-targets the whole book. Whatever stays uninvested is parked in SHV, a
T-bill ETF, which earns the broker's cash yield.

Departures from the component bots:
- Equal weights across components. A first-half inverse-vol fit is a later step,
  because it needs a per-component return series.
- Each component is called with its own defaults and its own replay. A component
  that raises contributes an empty book, so it cannot take the others down. It
  still counts in the divisor, so a failure lowers exposure instead of raising it.
- Volatility targeting and the SHV parking are applied after the average. The
  components themselves are not changed.
"""

from __future__ import annotations

import logging
from typing import ClassVar

import pandas as pd

from tradingbot import (
    quantsmd_bracket_rsi_reversion,
    quantsmd_bracket_trend,
    quantsmd_limit_rsi_dip,
    quantsmd_limit_trend_pullback,
    quantsmd_oco_dip_or_breakout,
    quantsmd_stop_breakout_entry,
    quantsmd_stop_limit_breakout,
    quantsmd_stop_limit_protection,
    quantsmd_stop_loss_protection,
    quantsmd_trailing_stop_limit,
    quantsmd_trailing_stop_rsi,
    quantsmd_trailing_stop_trend,
)
from tradingbot.utils.botclass import Bot
from tradingbot.utils.quantsmd_common import ETF3, scale_to_max_gross
from tradingbot.utils.quantsmd_orders import ohlc_frames
from tradingbot.utils.quantsmd_v2 import park_cash, vol_target_scale
from tradingbot.utils.runner import run_bot

logger = logging.getLogger(__name__)

SHV_SYMBOL = "SHV"
UNIVERSE = [*ETF3, SHV_SYMBOL]
COMPONENTS = (
    quantsmd_bracket_trend,
    quantsmd_bracket_rsi_reversion,
    quantsmd_stop_breakout_entry,
    quantsmd_stop_limit_breakout,
    quantsmd_trailing_stop_limit,
    quantsmd_trailing_stop_rsi,
    quantsmd_trailing_stop_trend,
    quantsmd_oco_dip_or_breakout,
    quantsmd_limit_trend_pullback,
    quantsmd_stop_loss_protection,
    quantsmd_stop_limit_protection,
    quantsmd_limit_rsi_dip,
)
TARGET_VOL = 0.10
VOL_WINDOW = 63
MAX_GROSS = 1.0
SLEEVE_BOOST = 4.0


def compute_weights(
    ohlc: dict[str, pd.DataFrame],
    target_vol: float = TARGET_VOL,
    vol_window: int = VOL_WINDOW,
    max_gross: float = MAX_GROSS,
    sleeve_boost: float = SLEEVE_BOOST,
) -> dict[str, float]:
    """Return the vol-targeted ensemble book, with any idle weight in ``SHV``.

    ``ohlc`` maps tickers to open/high/low/close frames up to the current bar. Each
    component's book is averaged per ticker over all components, multiplied by
    ``sleeve_boost`` and clipped to ``max_gross``. The result is then vol-targeted
    and the residual goes to ``SHV``. An empty result is ``{"SHV": 1.0}``.
    """
    books: list[dict[str, float]] = []
    for module in COMPONENTS:
        try:
            books.append(module.compute_weights(ohlc))
        except Exception:
            logger.exception("order-ensemble component %s failed; using an empty book", module.__name__)
            books.append({})

    n = len(books)
    averaged: dict[str, float] = {}
    for book in books:
        for ticker, weight in book.items():
            averaged[ticker] = averaged.get(ticker, 0.0) + float(weight) / n

    boosted = scale_to_max_gross({t: w * sleeve_boost for t, w in averaged.items()}, max_gross)
    closes = pd.DataFrame({t: ohlc[t]["close"] for t in ohlc})
    scaled = vol_target_scale(boosted, closes, target_vol, vol_window, max_gross)
    return park_cash(scaled, SHV_SYMBOL)


class QuantsmdOrderEnsembleBot(Bot):
    """
    Equal-weight average of twelve order-style quants.md books, vol-targeted, idle cash in SHV.

    Args:
        target_vol: Annualised volatility the whole book is scaled to.
        sleeve_boost: Multiplier on the averaged book, so one name held by every
            component reaches roughly 0.6-0.7 weight.
        vol_window: Trailing daily returns used to estimate the book's volatility.

    The ensemble is replayed on every run and never returns ``None``.
    """

    # The 63-bar volatility window and the 250-bar component replays need more than 1y.
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    param_grid: ClassVar[dict] = {
        "target_vol": [0.06, 0.10, 0.14],
        "sleeve_boost": [2.0, 4.0],
    }

    def __init__(
        self,
        target_vol: float = TARGET_VOL,
        sleeve_boost: float = SLEEVE_BOOST,
        vol_window: int = VOL_WINDOW,
        **kwargs,
    ):
        super().__init__(
            "QuantsmdOrderEnsembleBot",
            tickers=list(UNIVERSE),
            interval="1d",
            period="2y",
            target_vol=target_vol,
            sleeve_boost=sleeve_boost,
            vol_window=vol_window,
            benchmark_tickers=[],
            **kwargs,
        )
        self.target_vol = target_vol
        self.sleeve_boost = sleeve_boost
        self.vol_window = vol_window

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Return the ensemble book for the current bar."""
        ohlc = ohlc_frames(self.datas, self.tradeable_tickers)
        return compute_weights(
            ohlc,
            target_vol=self.target_vol,
            vol_window=self.vol_window,
            max_gross=MAX_GROSS,
            sleeve_boost=self.sleeve_boost,
        )


if __name__ == "__main__":
    run_bot(QuantsmdOrderEnsembleBot)
