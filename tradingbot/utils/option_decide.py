"""
One decision path for the option strategies: decide(market, holdings, rules) -> actions.

Each round-3 bot and the index-vol bot used to hold its logic inside
makeOneIteration, and the replay backtest re-implemented it in a loop of its
own. The copies drifted:
  * The replay index-vol never unwound on an inverted vol curve.
  * The replay cross-vol used close-to-close HAR where the bot used Yang-Zhang.
  * The replay earnings crush skipped the bot's front-expiry check.
The stock bots solved the same problem with targetWeights, one pure function
that live and backtest both call. This is that, for option books.

  * A strategy is a pure function in utils/option_strategies.py. It reads the
    market through `Market` (chains, vol indices, prices, event dates) and the
    book through `Holdings` (one options.OptionBook per underlying, the same
    object the live bot sees). It returns a list of actions.
  * An action carries intent and a budget: Open(pick, max_risk_usd),
    Close(underlying), Hedge(underlying, band, ww). Sizing stays in the
    executor, as it was live. Worst-case loss is taken at the prices the
    executor fills at, and it is capped by the free cash (units_for_risk).
  * Executors:
    - live: OptionStrategyBot in utils/option_strategy_bot.py;
    - replay: option_replay.execute, over stored chains;
    - synthetic: the index-vol backtest's decide mode.
    The same decide function runs in all three, so they cannot drift.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from . import options

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Actions
# ------------------------------------------------------------------


@dataclass(frozen=True)
class Open:
    """Open as many units of `pick` as max_risk_usd of worst-case loss (and free cash) allows."""

    pick: options.StructurePick
    max_risk_usd: float
    reason: str = ""


@dataclass(frozen=True)
class Close:
    """Flatten every option leg on `underlying`; include_stock also its shares (a hedge)."""

    underlying: str
    include_stock: bool = False
    reason: str = ""


@dataclass(frozen=True)
class Hedge:
    """Trade shares toward zero delta: a fixed $ band, or a Whalley-Wilmott band ww=(cost_frac, risk_aversion)."""

    underlying: str
    band_usd: float
    ww: tuple[float, float] | None = None


Action = Open | Close | Hedge


def summary(actions: list[Action]) -> tuple[int, int]:
    """(opens, closes) requested: how a bot's makeOneIteration return value is derived."""
    return sum(isinstance(a, Open) for a in actions), sum(isinstance(a, Close) for a in actions)


# ------------------------------------------------------------------
# What a strategy may read
# ------------------------------------------------------------------


class Market(ABC):
    """
    The market as of one moment. Executors implement the primitives. The event
    readers default to the DB-first helpers in utils/options, which work for
    any past date.
    """

    def __init__(self, today: date, now: datetime):
        self.today = today
        self.now = now

    @abstractmethod
    def chain(self, underlying: str, target_dte: int) -> options.ChainView | None:
        """The first expiry at least target_dte days out, or None when there is no chain."""

    @abstractmethod
    def vol_index(self, symbol: str) -> float | None:
        """A vol index level (^VIX, ^VIX3M, ^VVIX) as of now, None when unknown."""

    @abstractmethod
    def closes(self, underlying: str) -> pd.Series:
        """
        Every daily close up to today: the HAR forecast's input.

        All of it, not a recent window. The index-vol backtests that validated
        the live rules fit HAR on an expanding window from 1999. The live bot
        used to fetch 5 years, and on the same synthetic market that alone took
        its gated alpha t from 4.15 to 1.35 (2007-2026). Found by running the
        live decision through the synthetic market, 2026-09-28.
        """

    @abstractmethod
    def ohlc(self, symbols) -> dict[str, pd.DataFrame]:
        """Daily OHLC up to today (lower-case columns), per symbol: the Yang-Zhang input."""

    def risk_free_rate(self) -> float:
        return options.risk_free_rate()

    def earnings_events(self, underlying: str) -> list[tuple[date, bool | None]]:
        return options.earnings_events(underlying, today=self.today)

    def next_earnings_event(self, underlying: str) -> tuple[date, bool | None] | None:
        return options.next_earnings_event(underlying, self.today)

    def next_earnings_date(self, underlying: str) -> date | None:
        return options.next_earnings_date(underlying, self.today)

    def next_dividend(self, underlying: str) -> tuple[date, float] | None:
        return options.next_dividend(underlying, self.today)

    def next_macro_event(self) -> tuple[tuple[str, date] | None, int | None]:
        """((kind, date), sessions until it) of the next FOMC/CPI/NFP release; (None, None) if unknown."""
        from . import macro_calendar

        try:
            stale = macro_calendar.stale_warning(self.today)
            if stale:
                logger.warning(stale)
            event = macro_calendar.next_event(self.today)
            return event, macro_calendar.business_days_to_next_event(self.today) if event else None
        except Exception as exc:
            logger.warning("Macro calendar unavailable: %s", exc)
            return None, None

    def scan_scores(self) -> dict[str, float]:
        """|z| per name from the latest mispricing scan on or before today."""
        from . import mispricing_scan as ms

        return ms.latest_scan_scores(as_of=self.today)

    def implied_correlation_history(self, index: str) -> pd.Series:
        from . import vol_surface as vs

        return vs.implied_correlation_history(index, as_of=self.today)

    def market_caps(self, symbols) -> dict[str, float]:
        from .fundamentals import get_fundamentals_batch

        caps = get_fundamentals_batch(list(symbols), self.today, max_age_days=10)
        return {u: float((c or {}).get("market_cap") or 0.0) for u, c in caps.items()}

    def name_vol(self, underlying: str, target_dte: int, ohlc: pd.DataFrame | None, min_obs: int = 60):
        """(NameVol, view): IV, Yang-Zhang fair vol and z-score of one name on the expiry a bot would trade."""
        from . import mispricing_scan as ms
        from .option_rules import NameVol

        try:
            view = self.chain(underlying, target_dte)
        except Exception as exc:
            logger.warning("%s: no chain (%s)", underlying, exc)
            view = None
        if view is None:
            return NameVol(underlying, None, None, False), None
        return ms.name_vol_from_view(
            view,
            ohlc,
            self.today,
            min_obs,
            events=self.earnings_events(underlying),
            next_earnings=self.next_earnings_date(underlying),
        ), view


class Holdings(ABC):
    """The strategy's own book: one options.OptionBook per underlying."""

    @abstractmethod
    def underlyings(self) -> set[str]:
        """Underlyings with an option leg held."""

    @abstractmethod
    def book(self, underlying: str) -> options.OptionBook:
        """Positions, marks, entry value, P&L, DTE, net greeks and hedge shares on `underlying`."""

    @abstractmethod
    def opened_on(self, underlying: str) -> date | None:
        """When the current structure on `underlying` was opened (its earliest leg)."""

    @abstractmethod
    def flows_since(self, underlying: str, since: date) -> float:
        """Net cash from every trade on `underlying` (options and shares) since `since`."""

    @abstractmethod
    def equity(self) -> float:
        """Cash plus everything held, marked."""

    def structure_pnl(self, underlying: str, book: options.OptionBook) -> float:
        """
        P&L of a hedged structure: every cash flow since the open plus what is
        still held. A delta-hedged straddle has realized part of its P&L
        through its share trades, which the option legs' own P&L misses.
        """
        opened = self.opened_on(underlying)
        if opened is None:
            return book.pnl
        return self.flows_since(underlying, opened) + book.value + book.shares * book.spot
