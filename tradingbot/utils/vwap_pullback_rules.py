"""
VWAP pullback rules: buy the first pullback into VWAP *after* the opening range breaks up.

A plain VWAP cross is not traded — price crosses it ~5 times a day and the reel's
1,200-cross test lost 89R after costs. The opening-range break is the regime filter;
the pullback is the entry.

Long-only, one trade per session, entries at a bar's close (bars are stamped at their
start, so a bar is only used once it is complete — the live bot drops the forming bar).
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
    session_vwap,
    to_ny,
    walk_to_exit,
)


@dataclass(frozen=True)
class VWAPPullbackRules:
    or_minutes: int = 15
    swing_bars: int = 6
    target_r: float = 2.0
    min_risk_pct: float = 0.0005
    pullback_tol: float = 0.0003
    max_entry_minutes: int = 240
    flat_before_close_minutes: int = 10

    def simulate_session(self, bars: pd.DataFrame) -> SessionResult:
        session = session_bars(bars)
        if session.empty:
            raise ValueError("no regular-session bars")
        session = session.reset_index(drop=True)
        day = to_ny(_extract_timestamps(session).iloc[0]).date()

        orng = opening_range(session, self.or_minutes)
        vwap = session_vwap(session).reset_index(drop=True)
        ts = _extract_timestamps(session)
        open_ts = session_open_ts(session)
        or_end = open_ts + pd.Timedelta(minutes=self.or_minutes)
        entry_cutoff = open_ts + pd.Timedelta(minutes=self.max_entry_minutes)
        force = session_close_ts(session) - pd.Timedelta(minutes=self.flat_before_close_minutes)
        step = pd.Timedelta(minutes=bar_minutes(session))

        armed = False
        for pos in range(len(session)):
            t = to_ny(ts.iloc[pos])
            if t < or_end:
                continue
            if t + step > entry_cutoff or t >= force:
                break
            close = float(session["close"].iloc[pos])
            low = float(session["low"].iloc[pos])
            if not armed:
                armed = close > orng.high
                continue
            v = float(vwap.iloc[pos])
            if not (low <= v * (1 + self.pullback_tol) and close > v):
                continue
            lo = max(0, pos - self.swing_bars + 1)
            stop = float(session["low"].iloc[lo : pos + 1].min())
            risk = close - stop
            if risk <= 0 or risk / close < self.min_risk_pct:
                continue
            trade = walk_to_exit(session, pos, stop, close + self.target_r * risk, force)
            return SessionResult(date=day, trade=trade)
        return SessionResult(date=day, trade=None)
