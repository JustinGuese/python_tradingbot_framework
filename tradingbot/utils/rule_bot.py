"""
`RuleBot` — a Bot whose decisions come from a declarative spec instead of Python.

This is the bridge between the no-code builder and the existing framework. A spec
assembled in the app becomes a real `Bot`, so it backtests through `backtest_bot`
and trades through `Bot.run()` with no separate execution path. That matters more
than it sounds: a second, "simpler" evaluator for user strategies is exactly how a
platform ends up showing backtests that do not describe what it later runs.

Crossovers are the one thing the base class cannot express. `decisionFunction`
receives a single row, so "RSI crossed above 30" has nothing to compare against.
The fix is to precompute the previous bar as extra columns in `getYFDataWithTA` —
the one method *both* the backtest and live paths use to obtain data, which is why
the two cannot diverge:

    backtest_bot()    -> bot.getYFDataWithTA(...)   (backtest.py, both branches)
    makeOneIteration()-> self.getYFDataWithTA(...)  (botclass.py)
"""

import logging
from typing import Any

import pandas as pd

from .botclass import Bot
from .strategy_spec import PREV_PREFIX, StrategySpec, crossover_columns, decide, validate_spec

logger = logging.getLogger(__name__)


class RuleBot(Bot):
    """
    A Bot driven by a `StrategySpec`.

    Args:
        spec: A validated `StrategySpec`, or a raw dict which will be validated.
        name: The `bots.name` primary key. Use the `us_<uuid>` namespace for
              user strategies so they can never collide with the built-in bots.
        attach_db: When False, skip all database work in `Bot.__init__`.

    `attach_db=False` exists for the backtest API endpoint. `Bot.__init__` runs
    `init_db()` (DDL) and `BotRepository.create_or_get_bot()` (an INSERT that
    materialises a $10k paper portfolio), neither of which belongs in a request
    that only wants to simulate. A detached instance can be backtested but not
    run: `makeOneIteration` and `run` need the portfolio row.
    """

    def __init__(
        self,
        spec: StrategySpec | dict,
        name: str,
        *,
        attach_db: bool = True,
        **kwargs: Any,
    ):
        self.spec: StrategySpec = spec if isinstance(spec, StrategySpec) else validate_spec(spec)
        self._prev_columns: frozenset[str] = crossover_columns(self.spec)
        self.attached = attach_db

        if attach_db:
            super().__init__(
                name,
                tickers=list(self.spec.tickers),
                interval=self.spec.interval,
                period=self.spec.period,
                **kwargs,
            )
            return

        # Detached: mirror the attribute surface Bot.__init__ would have set, minus
        # anything that touches the database. backtest_bot is written to accept
        # instances built this way (see backtest.py's getattr on benchmark_tickers).
        self.bot_name = name
        self.tickers = list(self.spec.tickers)
        self.symbol = self.tickers[0] if len(self.tickers) == 1 else None
        self.benchmark_tickers = []
        self.interval = self.spec.interval
        self.period = self.spec.period
        self.params = dict(kwargs)
        self.data = None
        self.datas = {}
        self.datasettings = (None, None)
        # `dbBot` is deliberately left unset rather than set to None: it is the
        # portfolio row, and code that reaches for it on a detached bot is a bug.
        # An AttributeError names the missing attribute; a None would travel until
        # something dereferenced `.portfolio` far from the cause.

        from .data_service import DataService

        self._data_service = DataService()

    # ------------------------------------------------------------------ #
    #  Data                                                              #
    # ------------------------------------------------------------------ #

    def _add_previous_bar_columns(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Append `__prev_<column>` for every column a crossover condition reads.

        The shift is applied on the full frame in timestamp order, so each row
        carries its own predecessor and `decisionFunction` stays a pure function of
        one row. The first row's shifted values are NaN, which `evaluate_condition`
        treats as "no cross" — correct, since there is no previous bar to cross.
        """
        if not self._prev_columns or data is None or data.empty:
            return data

        present = [column for column in self._prev_columns if column in data.columns]
        missing = self._prev_columns - set(present)
        if missing:
            # Not fatal: safe_get yields NaN and the condition goes False. Warn
            # because it means the strategy is silently inert, not that it is safe.
            logger.warning(
                f"{getattr(self, 'bot_name', '?')}: crossover columns absent from data, "
                f"those conditions can never fire: {sorted(missing)}"
            )
        if not present:
            return data

        ordered = data.sort_values("timestamp") if "timestamp" in data.columns else data.sort_index()
        enriched = ordered.copy()
        for column in present:
            # Direct assignment rather than join/concat: those align on the index,
            # and a frame with duplicate index labels would expand cartesian-style.
            enriched[PREV_PREFIX + column] = ordered[column].shift(1)
        return enriched

    def getYFDataWithTA(
        self,
        symbol: str | None = None,
        interval: str = "1m",
        period: str = "1d",
        saveToDB: bool = False,
        features: list[str] | None = None,
    ) -> pd.DataFrame:
        data = super().getYFDataWithTA(
            symbol=symbol,
            interval=interval,
            period=period,
            saveToDB=saveToDB,
            features=features,
        )
        enriched = self._add_previous_bar_columns(data)

        # Repoint whatever the base class just cached at the enriched frame.
        # Identity comparison rather than re-deriving the base's caching conditions:
        # this stays correct if those conditions ever change, and leaves any other
        # cached frame alone. Without it, a decisionFunction reading self.data would
        # see a frame with no __prev_ columns while its row has them.
        if self.data is data:
            self.data = enriched
        for cached_symbol, cached in self.datas.items():
            if cached is data:
                self.datas[cached_symbol] = enriched
        return enriched

    # ------------------------------------------------------------------ #
    #  Decisions                                                         #
    # ------------------------------------------------------------------ #

    def decisionFunction(self, row: pd.Series) -> int:
        return decide(self.spec, row)

    def run(self) -> None:
        """Execute one live iteration. Requires a database-attached instance."""
        if not self.attached:
            raise RuntimeError(
                f"{self.bot_name}: this RuleBot was built with attach_db=False and has no "
                "portfolio row, so it can be backtested but not run. Construct it with "
                "attach_db=True to trade."
            )
        super().run()

    def __repr__(self) -> str:
        return f"RuleBot(name={getattr(self, 'bot_name', '?')!r}, tickers={list(self.spec.tickers)})"


def build_rule_bot(spec: StrategySpec | dict, name: str, *, attach_db: bool = True) -> RuleBot:
    """Convenience constructor, mostly so callers do not import the class directly."""
    return RuleBot(spec, name, attach_db=attach_db)
