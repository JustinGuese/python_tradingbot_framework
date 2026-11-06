"""
Pure rules for MacroEventSleeveBot (docs/backtests/event-macro-followup-2026-11.md).

Decided at a session's close, for the next session's exposure. `offset` is the session
count from the event day E to the bar day D (D after E is positive).

Legs (enter at the close shown, exit at the close of the last held session):
  FOMC -> UUP   enter offset 0, held through offset +2   => long UUP at offsets 0, +1
  CPI  -> TLT   enter offset 0, held through +1          => long TLT at offset 0
  FOMC -> TLT   enter offset -1, held through 0          => long TLT at offset -1
  FOMC -> GLD   enter offset -1, held through 0          => long GLD at offset -1
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import date

from tradingbot.utils.market_calendar import sessions_between

LEGS: tuple[tuple[str, str, frozenset[int]], ...] = (
    ("FOMC", "UUP", frozenset({0, 1})),
    ("CPI", "TLT", frozenset({0})),
    ("FOMC", "TLT", frozenset({-1})),
    ("FOMC", "GLD", frozenset({-1})),
)
ASSETS = ("UUP", "TLT", "GLD")


def active_legs(bar_day: date, events: Iterable[tuple[str, date]]) -> list[str]:
    """One asset symbol per leg that is on at `bar_day`'s close (an asset can repeat)."""
    out: list[str] = []
    for kind, event_day in events:
        if abs((bar_day - event_day).days) > 5:
            continue
        # sessions_between is end-exclusive; event_day -> bar_day counts sessions after the event.
        offset = sessions_between(event_day, bar_day)
        for leg_kind, asset, offsets in LEGS:
            if leg_kind == kind and offset in offsets:
                out.append(asset)
    return out


def target_weights(bar_day: date, events: Iterable[tuple[str, date]]) -> dict[str, float]:
    """Long-only weights summing to 1.0 when any leg is on, else {} (all cash).

    Concurrent legs split the book equally per leg, so a day with TLT twice holds 2/3 TLT.
    The backtest in the doc held each leg at 100%, so concurrent days there were over-levered
    and the live alpha is lower than the doc's pooled figure.
    """
    legs = Counter(active_legs(bar_day, events))
    total = sum(legs.values())
    return {a: n / total for a, n in legs.items()} if total else {}
