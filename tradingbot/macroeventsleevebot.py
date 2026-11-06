"""
MacroEventSleeveBot — holds UUP / TLT / GLD only around FOMC and CPI releases, cash otherwise.

Legs and evidence: docs/backtests/event-macro-followup-2026-11.md (alpha vs QQQ +2-4%/yr at
beta 0 before selection bias; UNPROVEN, legs were picked after seeing 40 variants). It is held
roughly 25-30 sessions a year, so the book is cash the rest of the time. This bot exists to
collect a genuine forward record. Paper only: judge it by the weekly alphareport, not by return.

Schedule: 5 21 * * 1-5 (after the US close; the decision uses today's close bar).
"""

import logging
from datetime import date

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.macro_calendar import macro_events_frame
from tradingbot.utils.macro_sleeve_rules import ASSETS, active_legs, target_weights
from tradingbot.utils.runner import run_bot

logger = logging.getLogger(__name__)


class MacroEventSleeveBot(Bot):
    BACKTEST_PERIOD = "max"

    def __init__(self, **kwargs):
        super().__init__("MacroEventSleeveBot", tickers=list(ASSETS), interval="1d", period="1mo", **kwargs)
        self._events: list[tuple[str, date]] | None = None

    def _load_events(self) -> list[tuple[str, date]]:
        if self._events is None:
            frame = macro_events_frame(("FOMC", "CPI"))
            if frame.empty:
                # An empty calendar would read as "no event today" and hold cash forever, silently.
                raise RuntimeError("macro_events has no FOMC/CPI rows; refusing to run on an empty calendar")
            self._events = [(k, d) for k, d in zip(frame["kind"], frame["event_date"], strict=True)]
        return self._events

    @staticmethod
    def _bar_day(row: pd.Series) -> date:
        ts = row["timestamp"] if "timestamp" in row.index else row.name  # live vs backtest index differently
        return pd.Timestamp(ts).date()

    def targetWeights(self, rows: dict[str, pd.Series]) -> dict[str, float]:
        day = self._bar_day(next(iter(rows.values())))
        weights = target_weights(day, self._load_events())
        logger.info(
            "%s: %s legs=%s -> %s", self.bot_name, day, active_legs(day, self._load_events()), weights or "cash"
        )
        return weights


if __name__ == "__main__":
    run_bot(MacroEventSleeveBot)
