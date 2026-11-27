"""TS151 Economic Announcement Bot (§19.5)."""

from __future__ import annotations

from typing import Any, ClassVar

from tradingbot.utils.ts151.base import Ctx, TS151Bot
from tradingbot.utils.ts151.common import is_live_as_of
from tradingbot.utils.ts151.signals_macro import econ_announcement_weights


class TS151EconAnnouncementBot(TS151Bot):
    """Pre-announcement drift strategy around FOMC dates (§19.5).

    Longs SPY on the business day(s) before and through the announcement date.
    Remains flat (in cash) outside the announcement window.
    """

    BOT_NAME = "ts151_econannouncementbot"
    DEFAULT_UNIVERSE = ("SPY",)
    ALLOW_SHORT = False
    LONG_ONLY = True
    MAX_GROSS = 1.0
    NEEDS_DB = True

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "kinds": ("FOMC",),
        "days_before": 1,
        "days_after": 0,
        "asset": "SPY",
    }

    param_grid: ClassVar[dict[str, list[Any]]] = {
        "kinds": [("FOMC",), ("FOMC", "CPI")],
        "days_before": [1, 2],
        "days_after": [0, 1],
        "asset": ["SPY"],
    }

    def __init__(
        self,
        *,
        interval: str = "1d",
        period: str | None = None,
        rebalance: str = "D",
        universe: str | tuple[str, ...] | list[str] | None = None,
        long_only: bool = True,
        park_cash: bool = False,
        **params: Any,
    ):
        super().__init__(
            interval=interval,
            period=period,
            rebalance=rebalance,
            universe=universe,
            long_only=long_only,
            park_cash=park_cash,
            **params,
        )
        self._events_cache = None

    def _get_events(self, as_of: Any = None):
        if self._events_cache is None:
            from tradingbot.utils.macro_calendar import macro_events_frame

            df = macro_events_frame(kinds=self.kinds)
            self._events_cache = df["event_date"].tolist() if not df.empty else []

        if not self._events_cache:
            if is_live_as_of(as_of):
                raise RuntimeError("macro_events empty")
            return None

        return self._events_cache

    def signal(self, ctx: Ctx) -> dict[str, float] | None:
        event_dates = getattr(ctx, "macro_events", None)
        if event_dates is None:
            event_dates = self._get_events(as_of=ctx.as_of)
        if event_dates is None:
            return None

        return econ_announcement_weights(
            as_of=ctx.as_of,
            event_dates=event_dates,
            days_before=self.days_before,
            days_after=self.days_after,
            asset=self.asset,
            gross=float(self.MAX_GROSS),
        )
