"""
SleeveBlendBot — five-sleeve blend combining macro trend, corporate events, sector trend,
and momentum rotation.

Weights (fit on H1 in docs/backtests/sleeve-blend-2026-11.md):
  1. ContinuousFuturesTrend (0.394): Monthly trend on ETF proxies, inverse-vol sized.
  2. buyback_sp100 (0.205): S&P 100 post-buyback announcement 10-session drift.
  3. SmaDailyGated (0.185): Daily 9-sector 50/200 SMA trend gated by SPY SMA200.
  4. QuarterlyTeCostGate (0.180): ETF8 quarterly momentum rotation.
  5. activist_13d (0.036): Schedule 13D activist filings 20-session drift.

Stateless live execution:
  * Each price sleeve's book is recomputed statelessly as of its last rebalance bar:
    ContinuousFuturesTrend truncates closes at the last month-first bar;
    QuarterlyTeCostGate truncates closes at the last quarter-first bar.
  * SmaDailyGated evaluates daily.
  * Event sleeves (buyback and activist 13D) use active filings as of the bar date;
    when no events are active, that sleeve's allocation remains cash.
  * Unallocated weight is parked in SHV using park_idle_cash.

Documented deviations:
  * Tracking-error early rebalance for QuarterlyTeCostGate is not used in the blend;
    quarterly timing is strictly observed to keep the blend stateless.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, ClassVar

import pandas as pd

from tradingbot.activist13dbot import (
    DEDUPE_DAYS,
    HOLD_SESSIONS,
    PLACEHOLDER,
    read_sc13d_events,
)
from tradingbot.activist13dbot import (
    MAX_WEIGHT as ACTIVIST_MAX_WEIGHT,
)
from tradingbot.buybackdriftbot import read_buyback_events
from tradingbot.quantsmd_continuous_futures_trend import (
    FAST as CFT_FAST,
)
from tradingbot.quantsmd_continuous_futures_trend import (
    SLOW as CFT_SLOW,
)
from tradingbot.quantsmd_continuous_futures_trend import (
    UNIVERSE as CFT_UNIVERSE,
)
from tradingbot.quantsmd_continuous_futures_trend import (
    VOL_WINDOW as CFT_VOL_WINDOW,
)
from tradingbot.quantsmd_continuous_futures_trend import (
    compute_weights as cft_compute_weights,
)
from tradingbot.quantsmd_quarterly_te_cost_gate import (
    CAP as QTE_CAP,
)
from tradingbot.quantsmd_quarterly_te_cost_gate import (
    LOOKBACK as QTE_LOOKBACK,
)
from tradingbot.quantsmd_quarterly_te_cost_gate import (
    MAX_ANNUAL_TURNOVER as QTE_MAX_ANNUAL_TURNOVER,
)
from tradingbot.quantsmd_quarterly_te_cost_gate import (
    TE_THRESHOLD as QTE_TE_THRESHOLD,
)
from tradingbot.quantsmd_quarterly_te_cost_gate import (
    TE_WINDOW as QTE_TE_WINDOW,
)
from tradingbot.quantsmd_quarterly_te_cost_gate import (
    TOP_N as QTE_TOP_N,
)
from tradingbot.quantsmd_quarterly_te_cost_gate import (
    UNIVERSE as QTE_UNIVERSE,
)
from tradingbot.quantsmd_quarterly_te_cost_gate import (
    compute_weights as qte_compute_weights,
)
from tradingbot.quantsmd_sma_daily_gated import (
    CAP as SMA_CAP,
)
from tradingbot.quantsmd_sma_daily_gated import (
    FAST as SMA_FAST,
)
from tradingbot.quantsmd_sma_daily_gated import (
    OFF_SCALE as SMA_OFF_SCALE,
)
from tradingbot.quantsmd_sma_daily_gated import (
    SLOW as SMA_SLOW,
)
from tradingbot.quantsmd_sma_daily_gated import (
    UNIVERSE as SMA_UNIVERSE,
)
from tradingbot.quantsmd_sma_daily_gated import (
    compute_weights as sma_compute_weights,
)
from tradingbot.utils.botclass import Bot
from tradingbot.utils.buyback_rules import active_symbols, dedupe_recent
from tradingbot.utils.cash_park import park_idle_cash
from tradingbot.utils.quantsmd_common import closes_frame
from tradingbot.utils.runner import run_bot
from tradingbot.utils.universes import SP100

logger = logging.getLogger(__name__)

SLEEVE_WEIGHTS: dict[str, float] = {
    "continuous_futures_trend": 0.394,
    "buyback_sp100": 0.205,
    "sma_daily_gated": 0.185,
    "quarterly_te_cost_gate": 0.180,
    "activist_13d": 0.036,
}

UNIVERSE: list[str] = list(
    dict.fromkeys(
        [
            *CFT_UNIVERSE,
            *SMA_UNIVERSE,
            *QTE_UNIVERSE,
            *SP100,
            "SPY",
            "SHV",
        ]
    )
)


def last_quarter_first_bar(index: pd.Index) -> pd.Timestamp:
    """Return the timestamp of the first trading bar in the current calendar quarter."""
    if len(index) == 0:
        raise ValueError("Cannot determine quarter on empty index")
    last_dt = pd.Timestamp(index[-1])
    target_year = last_dt.year
    target_quarter = last_dt.quarter
    for ts in index:
        t = pd.Timestamp(ts)
        if t.year == target_year and t.quarter == target_quarter:
            return t
    return last_dt


def last_month_first_bar(index: pd.Index) -> pd.Timestamp:
    """Return the timestamp of the first trading bar in the current calendar month."""
    if len(index) == 0:
        raise ValueError("Cannot determine month on empty index")
    last_dt = pd.Timestamp(index[-1])
    target_year = last_dt.year
    target_month = last_dt.month
    for ts in index:
        t = pd.Timestamp(ts)
        if t.year == target_year and t.month == target_month:
            return t
    return last_dt


def compose_weights(
    closes: pd.DataFrame,
    day: date | None = None,
    buyback_events: list[Any] | None = None,
    sc13d_events: list[Any] | None = None,
    sleeve_weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """Stateless composition of the five-sleeve blend.

    Sleeve weights (H1 fit):
      ContinuousFuturesTrend: 0.394
      buyback_sp100:          0.205
      SmaDailyGated:           0.185
      QuarterlyTeCostGate:     0.180
      activist_13d:            0.036
      Total:                   1.000

    Price sleeves are recomputed as of their last rebalance bar (last month-first
    bar for ContinuousFuturesTrend, last quarter-first bar for QuarterlyTeCostGate).
    Event sleeves use active buyback and 13D filings; absent events remain cash.
    All unallocated weight is parked in SHV.
    """
    if closes.empty:
        return {}

    weights = SLEEVE_WEIGHTS if sleeve_weights is None else sleeve_weights
    composed: dict[str, float] = {}

    # 1. ContinuousFuturesTrend (0.394)
    w_cft = weights.get("continuous_futures_trend", 0.0)
    if w_cft > 0:
        try:
            m_bar = last_month_first_bar(closes.index)
            cft_cols = [c for c in CFT_UNIVERSE if c in closes.columns]
            cft_closes = closes.loc[:m_bar, cft_cols]
            book_cft = cft_compute_weights(cft_closes, CFT_FAST, CFT_SLOW, CFT_VOL_WINDOW) or {}
            for sym, w in book_cft.items():
                composed[sym] = composed.get(sym, 0.0) + w * w_cft
        except Exception:
            logger.exception("Error computing ContinuousFuturesTrend sleeve")
            raise

    # 2. QuarterlyTeCostGate (0.180) - TE breach early rebalance is not used in the blend
    w_qte = weights.get("quarterly_te_cost_gate", 0.0)
    if w_qte > 0:
        try:
            q_bar = last_quarter_first_bar(closes.index)
            qte_cols = [c for c in (*QTE_UNIVERSE, "SPY") if c in closes.columns]
            qte_closes = closes.loc[:q_bar, qte_cols]
            state: dict[str, Any] = {"last_target": None, "turnover": []}
            book_qte = (
                qte_compute_weights(
                    qte_closes,
                    QTE_LOOKBACK,
                    QTE_TOP_N,
                    QTE_CAP,
                    QTE_TE_THRESHOLD,
                    QTE_TE_WINDOW,
                    QTE_MAX_ANNUAL_TURNOVER,
                    state,
                )
                or {}
            )
            for sym, w in book_qte.items():
                composed[sym] = composed.get(sym, 0.0) + w * w_qte
        except Exception:
            logger.exception("Error computing QuarterlyTeCostGate sleeve")
            raise

    # 3. SmaDailyGated (0.185)
    w_sma = weights.get("sma_daily_gated", 0.0)
    if w_sma > 0:
        try:
            sma_cols = [c for c in (*SMA_UNIVERSE, "SPY") if c in closes.columns]
            sma_closes = closes[sma_cols]
            book_sma = (
                sma_compute_weights(
                    sma_closes,
                    SMA_FAST,
                    SMA_SLOW,
                    SMA_CAP,
                    off_scale=SMA_OFF_SCALE,
                )
                or {}
            )
            for sym, w in book_sma.items():
                composed[sym] = composed.get(sym, 0.0) + w * w_sma
        except Exception:
            logger.exception("Error computing SmaDailyGated sleeve")
            raise

    # Determine reference date for event sleeves
    ref_date = day
    if ref_date is None:
        ref_date = pd.Timestamp(closes.index[-1]).date()

    # 4. buyback_sp100 (0.205)
    w_buyback = weights.get("buyback_sp100", 0.0)
    if w_buyback > 0 and buyback_events:
        try:
            active_bb = active_symbols(buyback_events, ref_date)
            if active_bb:
                n = len(active_bb)
                bb_w = min(1.0 / n, 0.5)
                for sym in active_bb:
                    composed[sym] = composed.get(sym, 0.0) + bb_w * w_buyback
        except Exception:
            logger.exception("Error computing buyback_sp100 sleeve")
            raise

    # 5. activist_13d (0.036)
    w_13d = weights.get("activist_13d", 0.0)
    if w_13d > 0 and sc13d_events:
        try:
            deduped = dedupe_recent(sc13d_events, DEDUPE_DAYS)
            active_13d = [s for s in active_symbols(deduped, ref_date, HOLD_SESSIONS) if s != PLACEHOLDER]
            if active_13d:
                n = len(active_13d)
                sc_w = min(1.0 / n, ACTIVIST_MAX_WEIGHT)
                for sym in active_13d:
                    composed[sym] = composed.get(sym, 0.0) + sc_w * w_13d
        except Exception:
            logger.exception("Error computing activist_13d sleeve")
            raise

    # Park uninvested weight into SHV
    return park_idle_cash(composed, proxy="SHV", max_gross=1.0)


class SleeveBlendBot(Bot):
    """Five-sleeve blend: ContinuousFuturesTrend, Buyback, SmaDailyGated, QuarterlyTeCostGate, Activist13D.

    Weights (fit on H1 in docs/backtests/sleeve-blend-2026-11.md):
      ContinuousFuturesTrend: 0.394
      buyback_sp100:          0.205
      SmaDailyGated:           0.185
      QuarterlyTeCostGate:     0.180
      activist_13d:            0.036

    Idle cash is parked in SHV.
    """

    MIN_UNIVERSE_COVERAGE: ClassVar[float] = 0.90
    BACKTEST_PERIOD: ClassVar[str | None] = "max"

    def __init__(
        self,
        buyback_events: list[Any] | None = None,
        sc13d_events: list[Any] | None = None,
        sleeve_weights: dict[str, float] | None = None,
        **kwargs,
    ):
        super().__init__(
            "SleeveBlendBot",
            tickers=list(UNIVERSE),
            interval="1d",
            period="3y",
            **kwargs,
        )
        self._buyback_events = buyback_events
        self._sc13d_events = sc13d_events
        self.sleeve_weights = sleeve_weights or dict(SLEEVE_WEIGHTS)

    def _load_buyback_events(self) -> list[Any]:
        if self._buyback_events is not None:
            return self._buyback_events
        # Do not swallow: a stale scanner must fail the run loudly, not silently empty the sleeve.
        return read_buyback_events()

    def _load_sc13d_events(self) -> list[Any]:
        if self._sc13d_events is not None:
            return self._sc13d_events
        # Do not swallow: a stale scanner must fail the run loudly, not silently empty the sleeve.
        return read_sc13d_events()

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        """Compute the composed five-sleeve blend weights with idle cash parked in SHV."""
        closes = closes_frame(self.datas, self.tradeable_tickers)
        day = None
        if rows:
            ts = next(iter(rows.values()))
            day = pd.Timestamp(ts["timestamp"] if "timestamp" in ts.index else ts.name).date()

        bb_events = self._load_buyback_events()
        sc_events = self._load_sc13d_events()

        return compose_weights(
            closes=closes,
            day=day,
            buyback_events=bb_events,
            sc13d_events=sc_events,
            sleeve_weights=self.sleeve_weights,
        )


if __name__ == "__main__":
    run_bot(SleeveBlendBot)
