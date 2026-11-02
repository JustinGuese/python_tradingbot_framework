"""
Opening-range breakout rules: buy the first close above the opening-range high.

Small target, wide stop: the reel's bot wins ~91% of the time at ~0.35R and loses
~1.05R. Win rate alone means nothing here; expectancy after costs is the test.

Long-only, one trade per session, entry only inside ``window_minutes`` of the open,
and the position is closed when the window ends. Entries are at a bar's close.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .intraday_session import (
    SessionResult,
    _extract_timestamps,
    bar_minutes,
    opening_range,
    session_bars,
    session_close_ts,
    session_open_ts,
    to_ny,
    walk_to_exit,
)


@dataclass(frozen=True)
class ORBRules:
    or_minutes: int = 15
    window_minutes: int = 90
    target_r: float = 0.35
    max_stop_pct: float = 0.006

    def simulate_session(self, bars: pd.DataFrame) -> SessionResult:
        session = session_bars(bars)
        if session.empty:
            raise ValueError("no regular-session bars")
        session = session.reset_index(drop=True)
        ts = _extract_timestamps(session)
        day = to_ny(ts.iloc[0]).date()

        orng = opening_range(session, self.or_minutes)
        open_ts = session_open_ts(session)
        or_end = open_ts + pd.Timedelta(minutes=self.or_minutes)
        window_end = min(open_ts + pd.Timedelta(minutes=self.window_minutes), session_close_ts(session))
        step = pd.Timedelta(minutes=bar_minutes(session))

        for pos in range(len(session)):
            t = to_ny(ts.iloc[pos])
            if t < or_end:
                continue
            if t + step > window_end:
                break
            close = float(session["close"].iloc[pos])
            if close <= orng.high:
                continue
            stop = max(orng.low, close * (1 - self.max_stop_pct))
            risk = close - stop
            if risk <= 0:
                continue
            trade = walk_to_exit(session, pos, stop, close + self.target_r * risk, window_end)
            return SessionResult(date=day, trade=trade)
        return SessionResult(date=day, trade=None)
