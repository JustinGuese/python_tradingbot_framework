"""
Live executor for the option strategies in utils/option_strategies.py.

OptionStrategyBot runs one decide function against the live market and its own
book, then executes the actions:
  * Open  -> open_structure (sized by worst-case loss at the fill prices, capped
    by free cash)
  * Close -> close_options
  * Hedge -> delta_hedge
makeOneIteration returns 1 if anything opened, -1 if anything closed, else 0,
as the bots always did.

OptionUniverseBot is the base for bots that trade many underlyings from one
book (cross-vol, the scanner, earnings crush, dispersion). It carries no dummy
symbol. Before it, those bots passed symbol="SPY" only to satisfy the
constructor, and run() logged their SPY position.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any, ClassVar

import pandas as pd

from . import mispricing_scan as ms
from . import options
from .botclass import Bot
from .option_decide import Action, Close, Hedge, Holdings, Market, Open
from .option_rules import daily_close

logger = logging.getLogger(__name__)


class LiveMarket(Market):
    """The market as the bot sees it right now: yfinance chains and prices, DB event tables."""

    def __init__(self, bot: Bot, now: datetime | None = None):
        now = now or datetime.now(UTC)
        super().__init__(options.utc_today(), now)
        self._bot = bot
        self._ohlc: dict[str, pd.DataFrame] = {}

    def chain(self, underlying: str, target_dte: int) -> options.ChainView | None:
        return options.load_chain(underlying, target_dte, today=self.today)

    def vol_index(self, symbol: str) -> float | None:
        try:
            value = float(self._bot.getLatestPrice(symbol))
        except Exception as exc:
            logger.warning("%s unavailable: %s", symbol, exc)
            return None
        return value if value > 0 else None

    def closes(self, underlying: str) -> pd.Series:
        # "max", not a recent window: see Market.closes.
        return daily_close(self._bot.getYFData(symbol=underlying, interval="1d", period="max", saveToDB=True))

    def ohlc(self, symbols: Iterable[str]) -> dict[str, pd.DataFrame]:
        missing = sorted(set(symbols) - set(self._ohlc))
        if missing:
            self._ohlc.update(ms.load_ohlc(missing))
        return {u: self._ohlc[u] for u in symbols if u in self._ohlc}


class LiveHoldings(Holdings):
    """The bot's own book, read from the bots row and its trades."""

    def __init__(self, bot: Bot):
        self._bot = bot

    def underlyings(self) -> set[str]:
        return options.option_underlyings(self._bot.dbBot.portfolio)

    def book(self, underlying: str) -> options.OptionBook:
        return self._bot.option_book(underlying)

    def opened_on(self, underlying: str):
        legs = options.option_legs(self._bot.dbBot.portfolio, underlying)
        dates = [d for d in (options.opened_on(self._bot.bot_name, k) for k in legs) if d]
        return min(dates, default=None)

    def flows_since(self, underlying: str, since) -> float:
        return options.structure_flows(self._bot.bot_name, underlying, since)

    def equity(self) -> float:
        return self._bot.portfolio_value()


class OptionStrategyBot(Bot):
    """
    A bot whose whole iteration is decide() + execute. Subclasses set RULES and
    implement decide(market, holdings). Everything strategy-specific stays
    in utils/option_strategies.py, where replay and backtests call it too.
    """

    INITIAL_CAPITAL: ClassVar[float] = 100_000.0
    OPTION_ROLL_DTE: ClassVar[int | None] = None
    RULES: ClassVar[Any] = None

    def decide(self, market: Market, holdings: Holdings) -> list[Action]:
        raise NotImplementedError

    def makeOneIteration(self, now: datetime | None = None) -> int:
        actions = self.decide(LiveMarket(self, now), LiveHoldings(self))
        opened, closed = self.execute(actions)
        return 1 if opened else (-1 if closed else 0)

    def execute(self, actions: list[Action]) -> tuple[int, int]:
        """Carry out actions in order. Returns (structures opened, underlyings closed)."""
        opened = closed = 0
        for action in actions:
            if isinstance(action, Close):
                self.close_options(action.underlying, include_stock=action.include_stock)
                closed += 1
            elif isinstance(action, Open):
                units = self.open_structure(action.pick, action.max_risk_usd)
                if units:
                    logger.info(
                        "Opened %d x %s on %s: %s", units, action.pick.legs, action.pick.underlying, action.reason
                    )
                    opened += 1
            elif isinstance(action, Hedge):
                self.delta_hedge(action.underlying, action.band_usd, ww=action.ww)
            else:
                raise TypeError(f"Unknown action {action!r}")
        return opened, closed


class OptionUniverseBot(OptionStrategyBot):
    """
    An OptionStrategyBot over many underlyings from one book. UNIVERSE is the
    names it may trade and DECIDE the strategy function, called as
    DECIDE(market, holdings, RULES, universe).
    """

    UNIVERSE: ClassVar[tuple[str, ...]] = ()
    DECIDE: ClassVar[Callable[..., list[Action]] | None] = None

    def __init__(self, name: str, **kwargs):
        super().__init__(name, interval="1d", period="1y", **kwargs)

    def universe(self) -> tuple[str, ...]:
        return self.UNIVERSE

    def decide(self, market: Market, holdings: Holdings) -> list[Action]:
        return type(self).DECIDE(market, holdings, self.RULES, self.universe())
