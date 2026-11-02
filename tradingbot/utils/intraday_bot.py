"""
Base for intraday bots whose whole strategy is an ``IntradayRules.simulate_session``.

Every run replays today's *completed* bars through the rules and trades the
difference between the book and what the replay says it should hold. Nothing
about the open trade (entry, stop, target) is persisted, so a crashed or
repeated run converges to the same book, and live uses the exact function the
backtest uses.

Guards that apply whether or not anything else is wrong: the forming bar is
never used, a stale feed trades nothing, a position is only opened if its entry
bar is recent (a pod that starts mid-session does not chase an old signal), and
the rules allow one trade per session.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pandas as pd

from tradingbot.utils.botclass import Bot
from tradingbot.utils.intraday_session import (
    NEW_YORK,
    IntradayRules,
    _extract_timestamps,
    bar_minutes,
    session_bars,
    session_close_ts,
    to_ny,
)

logger = logging.getLogger(__name__)

BAR_MINUTES = 5


class IntradayRulesBot(Bot):
    # A bar older than this (end of bar to now) means the feed is stale.
    MAX_FEED_LAG_MINUTES = 15
    # A signal whose entry bar closed longer ago than this is not chased.
    MAX_ENTRY_LAG_MINUTES = 10

    def __init__(self, name: str, rules: IntradayRules, symbol: str = "QQQ", **kwargs):
        super().__init__(name, symbol, interval=f"{BAR_MINUTES}m", period="5d", **kwargs)
        self.rules = rules

    def decide(self, data: pd.DataFrame, now: datetime, holding: bool, has_cash: bool) -> int:
        """1 buy, -1 sell, 0 do nothing. Pure given its arguments."""
        if data is None or data.empty:
            return 0
        now_ts = pd.Timestamp(now)
        now_ts = now_ts.tz_localize("UTC") if now_ts.tzinfo is None else now_ts
        now_ny = now_ts.tz_convert(NEW_YORK)

        ts = _extract_timestamps(data)
        complete = data[(ts + pd.Timedelta(minutes=BAR_MINUTES)).dt.tz_convert("UTC") <= now_ts.tz_convert("UTC")]
        session = session_bars(complete, now_ny.date()) if not complete.empty else complete
        if session.empty:
            # No bar of today's session yet (weekend, holiday, before the open).
            return -1 if holding and now_ny.date() > to_ny(ts.iloc[-1]).date() else 0

        step = pd.Timedelta(minutes=bar_minutes(session))
        last_end = to_ny(_extract_timestamps(session).iloc[-1]) + step
        in_session = now_ny < session_close_ts(session)
        if in_session and now_ny - last_end > pd.Timedelta(minutes=self.MAX_FEED_LAG_MINUTES):
            logger.warning("%s: feed stale (last bar ended %s, now %s) - no action", self.bot_name, last_end, now_ny)
            return 0

        trade = self.rules.simulate_session(session).trade
        want_long = trade is not None and trade.exit_reason == "open"
        if want_long and trade is not None:
            entry_end = to_ny(pd.Timestamp(trade.entry_ts)) + step
            fresh = now_ny - entry_end <= pd.Timedelta(minutes=self.MAX_ENTRY_LAG_MINUTES)
            if not holding and has_cash and fresh:
                return 1
            return 0
        return -1 if holding else 0

    def makeOneIteration(self) -> int:
        symbol = self.symbol
        if symbol is None:
            raise ValueError(f"{self.bot_name} needs a symbol")
        data = self.getYFData(interval=self.interval, period=self.period, saveToDB=True)
        portfolio = self.dbBot.portfolio
        holding = float(portfolio.get(symbol, 0)) > 1e-6
        has_cash = float(portfolio.get("USD", 0)) > 10
        decision = self.decide(data, datetime.now(UTC), holding, has_cash)
        if decision == 1:
            logger.info("%s: rules say long - buying %s", self.bot_name, symbol)
            self.buy(symbol)
        elif decision == -1:
            logger.info("%s: rules say flat - selling %s", self.bot_name, symbol)
            self.sell(symbol)
        return decision
